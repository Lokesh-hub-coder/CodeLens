from app.llm.prompts import SYSTEM_PROMPT


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
