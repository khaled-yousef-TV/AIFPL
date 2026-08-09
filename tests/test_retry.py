import httpx
import pytest

from aifpl.config import HttpRetrySettings
from aifpl.retry import retry_async, retry_sync


def transient_error() -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test")
    response = httpx.Response(503, request=request)
    return httpx.HTTPStatusError("unavailable", request=request, response=response)


def permanent_error() -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test")
    response = httpx.Response(400, request=request)
    return httpx.HTTPStatusError("bad request", request=request, response=response)


def test_sync_retry_uses_bounded_exponential_backoff() -> None:
    calls = 0
    delays: list[float] = []

    def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise transient_error()
        return "ok"

    result = retry_sync(operation, HttpRetrySettings(3, 0.25), sleep=delays.append)

    assert result == "ok"
    assert calls == 3
    assert delays == [0.25, 0.5]


def test_sync_retry_does_not_retry_permanent_http_error() -> None:
    calls = 0

    def operation() -> None:
        nonlocal calls
        calls += 1
        raise permanent_error()

    with pytest.raises(httpx.HTTPStatusError):
        retry_sync(operation, HttpRetrySettings(3, 0), sleep=lambda _: None)
    assert calls == 1


@pytest.mark.asyncio
async def test_async_retry_retries_transient_failure() -> None:
    calls = 0
    delays: list[float] = []

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise transient_error()
        return "ok"

    async def sleep(delay: float) -> None:
        delays.append(delay)

    result = await retry_async(operation, HttpRetrySettings(2, 0.5), sleep=sleep)

    assert result == "ok"
    assert delays == [0.5]
