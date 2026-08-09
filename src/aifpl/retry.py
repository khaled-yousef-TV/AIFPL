from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

from aifpl.config import HttpRetrySettings


T = TypeVar("T")


def is_transient_http_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.RequestError)


def retry_sync(
    operation: Callable[[], T], settings: HttpRetrySettings,
    should_retry: Callable[[Exception], bool] = is_transient_http_error,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    for attempt in range(settings.attempts):
        try:
            return operation()
        except Exception as exc:
            if attempt + 1 >= settings.attempts or not should_retry(exc):
                raise
            sleep(settings.base_delay_seconds * (2**attempt))
    raise AssertionError("retry loop did not return or raise")


async def retry_async(
    operation: Callable[[], Awaitable[T]], settings: HttpRetrySettings,
    should_retry: Callable[[Exception], bool] = is_transient_http_error,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    for attempt in range(settings.attempts):
        try:
            return await operation()
        except Exception as exc:
            if attempt + 1 >= settings.attempts or not should_retry(exc):
                raise
            await sleep(settings.base_delay_seconds * (2**attempt))
    raise AssertionError("retry loop did not return or raise")
