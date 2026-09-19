from app.generator import _build_prompt


def test_build_prompt_warns_against_guessing_a_truncated_fact():
    prompt = _build_prompt("What is the price?", [("node-1", "The price was 4.")])

    assert "truncat" in prompt.lower() or "cut off" in prompt.lower(), (
        "expected the prompt to warn the Generator against guessing a numeric fact "
        "that looks truncated in the Retrieved Context"
    )
