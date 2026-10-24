from app.llm.prompts import STRUCTURED_SYSTEM_PROMPT, SYSTEM_PROMPT


def test_system_prompt_requires_plain_text_structured_answers():
    section_labels = (
        "Direct Answer:",
        "Exact Location:",
        "Root Cause and Causal Mechanism:",
        "Recommended Fix:",
    )

    positions = [SYSTEM_PROMPT.index(label) for label in section_labels]

    assert positions == sorted(positions)
    assert SYSTEM_PROMPT.isascii()
    assert "Do not use Markdown headings, bold or italic emphasis" in SYSTEM_PROMPT
    assert 'numbered steps written as "1.", "2."' in SYSTEM_PROMPT
    assert 'say "Not shown in the provided excerpt."' in SYSTEM_PROMPT


def test_structured_prompt_requires_json_and_limits_evidence():
    assert "exactly one JSON object" in STRUCTURED_SYSTEM_PROMPT
    assert "Do not invent file paths, line numbers, method names, causes, or fixes." in STRUCTURED_SYSTEM_PROMPT
    assert STRUCTURED_SYSTEM_PROMPT.isascii()
