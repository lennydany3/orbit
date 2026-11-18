import pytest

from conftest import CURRENT_STEP, load_cases
from orbit.agent import rules

RULE_CASES = [c for c in load_cases() if c["via"] == "rules"]


@pytest.mark.parametrize("case", RULE_CASES, ids=lambda c: c["request"])
def test_rule_cases_from_yaml(case):
    if case["step"] > CURRENT_STEP:
        pytest.skip(f"needs step {case['step']}")
    m = rules.match(case["request"])
    assert m is not None, "rule did not match"
    expected = case["expect"][0]
    assert (m.tool, m.args) == (expected["tool"], expected["args"])


def test_case_insensitive_and_trimmed():
    m = rules.match("  ECHO Hello  ")
    assert m and m.args == {"text": "Hello"}


@pytest.mark.parametrize("text", ["", "hello there", "echo", "please echo hi", "open spotify and play music", "open github, then search cats", "search cats on youtube and fullscreen the first video", "search pizza and pasta"])
def test_no_match_goes_to_llm(text):
    assert rules.match(text) is None
