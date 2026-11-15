"""Downloading a file from Telegram survives a brief failure and never leaks the token."""

import httpx
import pytest

from backend.ingestion import telegram

TOKEN = "123456:SECRET-TOKEN"


class FakeClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def get(self, url, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _response(status=200, json=None, content=b""):
    request = httpx.Request("GET", f"https://api.telegram.org/bot{TOKEN}/getFile")
    return httpx.Response(status, json=json, request=request) if json is not None else httpx.Response(
        status, content=content, request=request)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def instant(_):
        return None
    monkeypatch.setattr(telegram.asyncio, "sleep", instant)


@pytest.mark.asyncio
async def test_a_timeout_is_retried_and_the_file_is_returned():
    client = FakeClient([httpx.ReadTimeout(""), _response(json={"result": {"file_path": "voice/a.oga"}}),
                         _response(content=b"audio")])
    assert await telegram.download_telegram_file("f1", TOKEN, client) == b"audio"
    assert client.calls == 3


@pytest.mark.asyncio
async def test_repeated_failure_gives_a_clear_message_without_the_token():
    client = FakeClient([httpx.ReadTimeout("")] * 3)
    with pytest.raises(RuntimeError) as err:
        await telegram.download_telegram_file("f1", TOKEN, client)
    assert "send it again" in str(err.value) and "ReadTimeout" in str(err.value)
    assert TOKEN not in str(err.value)
    assert client.calls == 3


@pytest.mark.asyncio
async def test_a_refusal_is_not_retried_and_does_not_leak_the_token():
    client = FakeClient([_response(status=400, json={"ok": False})])
    with pytest.raises(RuntimeError) as err:
        await telegram.download_telegram_file("f1", TOKEN, client)
    assert "HTTP 400" in str(err.value) and TOKEN not in str(err.value)
    assert client.calls == 1
