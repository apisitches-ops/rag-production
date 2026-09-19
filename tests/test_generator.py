from app.generator import _build_prompt


def test_build_prompt_warns_against_guessing_a_truncated_fact():
    prompt = _build_prompt("What is the price?", [("node-1", "The price was 4.")])

    assert "truncat" in prompt.lower() or "cut off" in prompt.lower(), (
        "expected the prompt to warn the Generator against guessing a numeric fact "
        "that looks truncated in the Retrieved Context"
    )


def test_build_prompt_never_lets_one_excerpts_tail_touch_the_next_excerpts_head():
    # Reproduces #31's real failure: one Node ends "...averaged 6." and an
    # unrelated Node (about something else entirely) happens to start "3
    # million barrels...". Joined bare, that reads as "6.\n\n3 million" -
    # the Generator can misread it as one continuous "6.3" fact spanning two
    # unrelated Nodes, not a truncated number it needs to guess the digits
    # of (a different failure mode from the one the test above covers).
    prompt = _build_prompt(
        "question",
        [
            ("node-1", "The refining margin averaged 6."),
            ("node-2", "3 million barrels per day of unrelated demand growth."),
        ],
    )

    assert "averaged 6.\n\n3 million" not in prompt, (
        "expected a clear boundary between excerpts so a truncated tail can't "
        "visually blend into an unrelated excerpt's head"
    )
