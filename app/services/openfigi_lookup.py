"""WKN → ticker resolution via the OpenFIGI mapping API."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"


async def resolve_wkn(wkn: str, api_key: str = "") -> str | None:
    """Return the ticker symbol for a WKN, or None if it cannot be resolved.

    Args:
        wkn: The 6-character WKN (Wertpapierkennnummer) to resolve.
        api_key: Optional OpenFIGI API key for higher rate limits.
                 Unauthenticated requests are limited to 25 req/min.

    Returns:
        The resolved ticker symbol (upper-case), or None on failure.
    """
    wkn = wkn.strip().upper()
    if not wkn:
        return None

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["X-OPENFIGI-APIKEY"] = api_key

    payload = [{"idType": "ID_WERTPAPIER", "idValue": wkn}]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(_OPENFIGI_URL, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        logger.warning("OpenFIGI request failed for WKN %s: %s", wkn, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Unexpected error during OpenFIGI lookup for WKN %s: %s", wkn, exc)
        return None

    # Response shape: [{"data": [{"ticker": "AAPL", ...}]}]
    try:
        ticker = data[0]["data"][0]["ticker"]
        return str(ticker).upper() if ticker else None
    except (KeyError, IndexError, TypeError):
        return None
