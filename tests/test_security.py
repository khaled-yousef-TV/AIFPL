import os

import httpx
import pytest

from aifpl.odds import OddsSourceError, TheOddsApiClient
from aifpl.security import redact_secrets


def test_secret_redaction_removes_query_and_environment_values(monkeypatch) -> None:
    monkeypatch.setenv("ODDS_API_KEY", "super-secret-value")

    message = redact_secrets("failed https://example.test?apiKey=super-secret-value&region=uk")

    assert "super-secret-value" not in message
    assert "[REDACTED]" in message


def test_odds_http_error_never_exposes_api_key(monkeypatch) -> None:
    monkeypatch.setenv("AIFPL_HTTP_RETRY_ATTEMPTS", "1")

    def fail(*args, **kwargs):
        request = httpx.Request("GET", "https://example.test?apiKey=super-secret-value")
        response = httpx.Response(401, request=request)
        raise httpx.HTTPStatusError("failed", request=request, response=response)

    monkeypatch.setattr(httpx, "get", fail)
    with pytest.raises(OddsSourceError) as caught:
        TheOddsApiClient("super-secret-value", base_url="https://example.test").fetch_epl_h2h()

    assert "super-secret-value" not in str(caught.value)
