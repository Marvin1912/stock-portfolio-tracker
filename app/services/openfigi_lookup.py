"""WKN → ticker resolution via the OpenFIGI mapping API."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"

# Mapping from OpenFIGI exchCode to yfinance ticker suffix.
# Ordered by preference: primary/most-liquid exchanges first.
# exchCodes not listed here are treated as US-listed (no suffix needed).
_EXCHCODE_TO_SUFFIX: dict[str, str] = {
    # Germany
    "GR": ".DE",   # XETRA (primary German exchange)
    "GF": ".F",    # Frankfurt
    "GM": ".MU",   # Munich
    "GY": ".SG",   # Stuttgart
    "GS": ".SG",   # Stuttgart (alt code)
    "GH": ".HM",   # Hamburg
    "GI": ".HM",   # Hamburg (alt code)
    "GD": ".DU",   # Düsseldorf
    # Austria
    "AV": ".VI",   # Vienna
    # Switzerland
    "SW": ".SW",   # SIX Swiss Exchange
    # UK
    "LN": ".L",    # London Stock Exchange
    # France
    "FP": ".PA",   # Euronext Paris
    # Netherlands
    "NA": ".AS",   # Euronext Amsterdam
    # Italy
    "IM": ".MI",   # Borsa Italiana Milan
    # Spain
    "SM": ".MC",   # Madrid
    # Belgium
    "BB": ".BR",   # Euronext Brussels
    # Portugal
    "PL": ".LS",   # Euronext Lisbon
    # Sweden
    "SS": ".ST",   # Stockholm
    # Norway
    "NO": ".OL",   # Oslo
    # Denmark
    "DC": ".CO",   # Copenhagen
    # Finland
    "FH": ".HE",   # Helsinki
    # Australia
    "AT": ".AX",   # ASX
    # Japan
    "JT": ".T",    # Tokyo
    # Hong Kong
    "HK": ".HK",   # Hong Kong
    # Canada
    "CT": ".TO",   # Toronto
    # Mexico
    "MM": ".MX",   # Mexico
}

# Preferred exchCodes in priority order when multiple results are returned.
# We prefer the most liquid / primary listing for each region.
_PREFERRED_EXCHCODES = [
    "GR",   # XETRA — primary German exchange
    "LN",   # London
    "FP",   # Paris
    "NA",   # Amsterdam
    "IM",   # Milan
    "SM",   # Madrid
    "SW",   # Swiss
    "AV",   # Vienna
    "AT",   # ASX
    "JT",   # Tokyo
    "HK",   # Hong Kong
    "CT",   # Toronto
    "US",   # US (no suffix)
]


def _build_yfinance_ticker(ticker: str, exch_code: str) -> str:
    """Append the appropriate yfinance exchange suffix for the given exchCode."""
    suffix = _EXCHCODE_TO_SUFFIX.get(exch_code, "")
    return f"{ticker}{suffix}"


async def resolve_wkn(wkn: str, api_key: str = "") -> str | None:
    """Return the ticker symbol for a WKN, or None if it cannot be resolved.

    The returned ticker includes the yfinance exchange suffix (e.g. ``RHM.DE``
    for Rheinmetall AG on XETRA) so that yfinance can unambiguously identify
    the correct security.

    Args:
        wkn: The 6-character WKN (Wertpapierkennnummer) to resolve.
        api_key: Optional OpenFIGI API key for higher rate limits.
                 Unauthenticated requests are limited to 25 req/min.

    Returns:
        The resolved ticker symbol (upper-case, with exchange suffix), or None
        on failure.
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

    # Response shape: [{"data": [{"ticker": "RHM", "exchCode": "GR", ...}]}]
    try:
        results: list[dict[str, Any]] = data[0]["data"]
    except (KeyError, IndexError, TypeError):
        return None

    if not results:
        return None

    # Build a lookup: exchCode → first matching result
    by_exch: dict[str, dict[str, Any]] = {}
    for item in results:
        code = item.get("exchCode", "")
        if code and code not in by_exch:
            by_exch[code] = item

    # Pick the best result according to our preference order
    chosen: dict[str, Any] | None = None
    for preferred in _PREFERRED_EXCHCODES:
        if preferred in by_exch:
            chosen = by_exch[preferred]
            break

    # Fall back to the first result if none of the preferred codes matched
    if chosen is None:
        chosen = results[0]

    ticker = chosen.get("ticker")
    if not ticker:
        return None

    exch_code = str(chosen.get("exchCode", ""))
    yf_ticker = _build_yfinance_ticker(str(ticker).upper(), exch_code)
    logger.debug(
        "WKN %s resolved to OpenFIGI ticker %s (exchCode=%s) → yfinance ticker %s",
        wkn,
        ticker,
        exch_code,
        yf_ticker,
    )
    return yf_ticker
