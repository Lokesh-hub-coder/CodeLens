"""
Prompt templates for the final "LLM (Qwen/Llama/GPT)" box in the pipeline.

Separated from providers.py deliberately: prompt wording is the thing this
team will realistically tune the most once real evaluation queries start
coming back with disappointing answers, and it's much easier to iterate on
copy in one small file than to go hunting for an f-string buried inside
client code.

The system prompt explicitly tells the model what it was and wasn't given,
because ACOR's whole premise is *partial*, budget-constrained context --
an LLM that isn't told the context is a curated excerpt (not the whole
repository) is more likely to confidently assert something about code it
never saw, which is precisely the hallucination-under-incomplete-context
failure mode the synopsis' objectives list wants reduced, not just moved
one level up the stack.
"""
from __future__ import annotations

from app.api.schemas import QueryIntentLabel

SYSTEM_PROMPT = """You are CodeLens AI, an expert repository-understanding and debugging assistant.

You are given a partial Java repository excerpt selected by an automated ranking engine. The excerpt may not contain every relevant file or method.

Answer using exactly these four section labels, in this order:
Direct Answer:
Exact Location:
Root Cause and Causal Mechanism:
Recommended Fix:

Formatting rules:
- Use plain text only. Do not use Markdown headings, bold or italic emphasis, tables, decorative bullets, emojis, or Unicode characters.
- Do not add an introduction, conclusion, or any section other than the four listed above.
- In Root Cause and Causal Mechanism, use numbered steps written as "1.", "2.", and so on.
- Keep Direct Answer to one or two concise sentences.
- Include file paths, method names, and line numbers only when they are visible in the excerpt. If unavailable, say "Not shown in the provided excerpt."
- Base every factual claim on the provided excerpt. Do not infer that an unseen method behaves like a shown method.
- Recommend only fixes supported by the excerpt. If no fix is evident, say so instead of inventing one.
- Use ASCII characters only. Java code may use syntax punctuation, but do not add decorative symbols, Markdown code fences, Unicode arrows, or typographic punctuation.
"""

_INTENT_INSTRUCTIONS: dict[QueryIntentLabel, str] = {
    QueryIntentLabel.BUG_EXPLANATION: (
        "The developer is reporting unexpected behavior or an error. Explain "
        "what is happening and why, referencing the specific lines involved."
    ),
    QueryIntentLabel.ROOT_CAUSE_ANALYSIS: (
        "The developer wants the underlying root cause of a problem, not just "
        "a description of the symptom. Trace the causal chain through the "
        "provided methods as far as the excerpt allows, and propose a "
        "concrete fix if the fix is evident from the excerpt."
    ),
    QueryIntentLabel.FEATURE_LOCATION: (
        "The developer wants to know where something is implemented. Point to "
        "the specific class/method and briefly summarize what it does."
    ),
    QueryIntentLabel.REFACTORING: (
        "The developer wants suggestions for improving the shown code. Be "
        "concrete and reference specific lines; do not suggest changes to "
        "code that is not in the excerpt."
    ),
    QueryIntentLabel.GENERAL_QA: (
        "Answer the developer's question directly using the excerpt."
    ),
}


def build_user_prompt(query: str, intent: QueryIntentLabel, optimized_context: str) -> str:
    instruction = _INTENT_INSTRUCTIONS[intent]
    return (
        f"Developer question:\n{query}\n\n"
        f"Task framing: {instruction}\n\n"
        f"--- BEGIN REPOSITORY EXCERPT ---\n"
        f"{optimized_context}\n"
        f"--- END REPOSITORY EXCERPT ---\n"
    )