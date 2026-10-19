import pytest

from bridge.router.shadow_worker import _strict_source


SOURCE = "def square(n):\n    return n * n\n"
JSON_ANSWER = '{"answer":"def square(n):\\n    return n * n\\n"}'


def test_plain_json_envelope_keeps_source_and_records_format():
    assert _strict_source(JSON_ANSWER, prompt_version="complete-module-envelope-v2") == (
        SOURCE, "json")


def test_one_exact_fenced_json_envelope_is_accepted():
    assert _strict_source("```json\r\n" + JSON_ANSWER + "\r\n```\r\n",
        prompt_version="complete-module-envelope-v2") == (
        SOURCE, "fenced_json")


def test_original_prompt_version_still_rejects_fenced_json():
    with pytest.raises(ValueError):
        _strict_source("```json\n" + JSON_ANSWER + "\n```",
            prompt_version="complete-module-v1")


@pytest.mark.parametrize("raw", [
    "Here is the answer:\n```json\n" + JSON_ANSWER + "\n```",
    "```json\n" + JSON_ANSWER + "\n```\nextra",
    "```python\n" + JSON_ANSWER + "\n```",
    "```json\n" + JSON_ANSWER + "\n```\n```json\n" + JSON_ANSWER + "\n```",
    "```json\n{\"answer\":\"x\",\"answer\":\"y\"}\n```",
    "```json\n{\"answer\":42}\n```",
])
def test_fenced_envelope_rejects_chatter_duplicates_and_wrong_shape(raw):
    with pytest.raises(ValueError):
        _strict_source(raw, prompt_version="complete-module-envelope-v2")
