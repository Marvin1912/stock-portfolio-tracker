"""Unit tests for the OpenFIGI WKN → ticker resolution service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.openfigi_lookup import resolve_wkn

pytestmark = pytest.mark.asyncio


def _mock_response(json_data: object, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.json.return_value = json_data
    response.raise_for_status = MagicMock()
    if status_code >= 400:
        import httpx

        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=MagicMock()
        )
    return response


@pytest.fixture
def mock_post():
    """Patch httpx.AsyncClient.post and yield the mock."""
    with patch("app.services.openfigi_lookup.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        yield mock_client


async def test_resolve_wkn_success(mock_post: AsyncMock) -> None:
    """Valid WKN returns the ticker from OpenFIGI."""
    mock_post.post = AsyncMock(
        return_value=_mock_response([{"data": [{"ticker": "AAPL", "figi": "BBG000B9XRY4"}]}])
    )
    result = await resolve_wkn("840400")
    assert result == "AAPL"


async def test_resolve_wkn_uppercase(mock_post: AsyncMock) -> None:
    """Ticker returned by OpenFIGI is normalised to upper-case."""
    mock_post.post = AsyncMock(
        return_value=_mock_response([{"data": [{"ticker": "msft"}]}])
    )
    result = await resolve_wkn("870747")
    assert result == "MSFT"


async def test_resolve_wkn_not_found(mock_post: AsyncMock) -> None:
    """OpenFIGI returns no data → None."""
    mock_post.post = AsyncMock(return_value=_mock_response([{}]))
    result = await resolve_wkn("000000")
    assert result is None


async def test_resolve_wkn_empty_data_list(mock_post: AsyncMock) -> None:
    """OpenFIGI returns empty data array → None."""
    mock_post.post = AsyncMock(return_value=_mock_response([{"data": []}]))
    result = await resolve_wkn("000000")
    assert result is None


async def test_resolve_wkn_http_error(mock_post: AsyncMock) -> None:
    """HTTP error from OpenFIGI → None (no exception propagated)."""
    import httpx

    mock_post.post = AsyncMock(
        side_effect=httpx.HTTPError("connection error")
    )
    result = await resolve_wkn("840400")
    assert result is None


async def test_resolve_wkn_empty_string() -> None:
    """Empty WKN returns None without making any HTTP call."""
    result = await resolve_wkn("")
    assert result is None


async def test_resolve_wkn_whitespace_only() -> None:
    """Whitespace-only WKN returns None without making any HTTP call."""
    result = await resolve_wkn("   ")
    assert result is None


async def test_resolve_wkn_sends_api_key(mock_post: AsyncMock) -> None:
    """When an API key is provided it is sent as X-OPENFIGI-APIKEY header."""
    mock_post.post = AsyncMock(
        return_value=_mock_response([{"data": [{"ticker": "AAPL"}]}])
    )
    await resolve_wkn("840400", api_key="my-secret-key")
    _, kwargs = mock_post.post.call_args
    assert kwargs["headers"]["X-OPENFIGI-APIKEY"] == "my-secret-key"


async def test_resolve_wkn_no_api_key_header_when_empty(mock_post: AsyncMock) -> None:
    """When no API key is given the X-OPENFIGI-APIKEY header is absent."""
    mock_post.post = AsyncMock(
        return_value=_mock_response([{"data": [{"ticker": "AAPL"}]}])
    )
    await resolve_wkn("840400", api_key="")
    _, kwargs = mock_post.post.call_args
    assert "X-OPENFIGI-APIKEY" not in kwargs["headers"]
