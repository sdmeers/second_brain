"""Book Mode must only ever call the configured model.

It used to hardcode `gemini-2.5-flash` and a `gemini-2.0-flash` last resort; Google retired
the latter, so that path could only fail, and reroutes the former to a dearer model.
"""

import asyncio
from types import SimpleNamespace

import pytest

from backend import add_book
from backend.config import GEMINI_MODEL

BOOK_TEXT = "# A Book\n\n" + "A long enough summary of the book. " * 5


def _run(monkeypatch, fake_call):
    calls = []

    async def recording_call(model, contents, config=None, max_retries=5):
        calls.append(SimpleNamespace(model=model, config=config))
        return await fake_call(len(calls))

    async def no_cache(model, system_instruction):
        calls.append(SimpleNamespace(model=model, config=None))
        return None

    monkeypatch.setattr(add_book, "call_gemini_with_retry", recording_call)
    monkeypatch.setattr(add_book, "get_or_create_context_cache", no_cache)
    monkeypatch.setattr(add_book, "get_existing_concepts", lambda: [])
    result = asyncio.run(add_book.generate_book_node("A Book", "An Author", []))
    return result, calls


def test_search_attempt_uses_configured_model(monkeypatch):
    async def fake_call(n):
        return SimpleNamespace(text=BOOK_TEXT, candidates=[])

    result, calls = _run(monkeypatch, fake_call)

    assert [c.model for c in calls] == [GEMINI_MODEL]
    assert calls[0].config.tools, "first attempt should be grounded with search"
    assert "# A Book" in result.summary_markdown


def test_rate_limited_search_retries_same_model_without_search(monkeypatch):
    async def fake_call(n):
        if n == 1:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return SimpleNamespace(text='{"summary_markdown": "# A Book", "concepts": []}', candidates=[])

    result, calls = _run(monkeypatch, fake_call)

    assert {c.model for c in calls} == {GEMINI_MODEL}
    assert not calls[-1].config.tools
    assert "# A Book" in result.summary_markdown


def test_empty_search_response_falls_back(monkeypatch):
    async def fake_call(n):
        if n == 1:
            return SimpleNamespace(text="", candidates=[])
        return SimpleNamespace(text='{"summary_markdown": "# A Book", "concepts": []}', candidates=[])

    result, calls = _run(monkeypatch, fake_call)

    assert "# A Book" in result.summary_markdown


def test_failure_of_both_attempts_surfaces(monkeypatch):
    async def fake_call(n):
        raise RuntimeError("503 UNAVAILABLE")

    with pytest.raises(RuntimeError, match="503"):
        _run(monkeypatch, fake_call)


def test_unrelated_error_is_not_retried(monkeypatch):
    seen = []

    async def fake_call(n):
        seen.append(n)
        raise RuntimeError("400 INVALID_ARGUMENT")

    with pytest.raises(RuntimeError, match="400"):
        _run(monkeypatch, fake_call)
    assert seen == [1]
