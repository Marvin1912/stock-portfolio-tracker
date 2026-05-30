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


_cache: dict[tuple[str, str], str | None] = {}


def _build_yfinance_ticker(ticker: str, exch_code: str) -> str:
    """Append the appropriate yfinance exchange suffix for the given exchCode."""
    suffix = _EXCHCODE_TO_SUFFIX.get(exch_code, "")
    return f"{ticker}{suffix}"


async def _resolve_via_openfigi(
    id_type: str, id_value: str, api_key: str
) -> str | None:
    """Post one id to OpenFIGI and return the preferred yfinance ticker."""
    cache_key = (id_type, id_value)
    if cache_key in _cache:
        logger.debug("OpenFIGI cache hit for %s %s", id_type, id_value)
        return _cache[cache_key]

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["X-OPENFIGI-APIKEY"] = api_key

    payload = [{"idType": id_type, "idValue": id_value}]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(_OPENFIGI_URL, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        logger.warning("OpenFIGI request failed for %s %s: %s", id_type, id_value, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Unexpected error during OpenFIGI lookup for %s %s: %s",
            id_type,
            id_value,
            exc,
        )
        return None

    try:
        results: list[dict[str, Any]] = data[0]["data"]
    except (KeyError, IndexError, TypeError):
        _cache[cache_key] = None
        return None

    if not results:
        _cache[cache_key] = None
        return None

    by_exch: dict[str, dict[str, Any]] = {}
    for item in results:
        code = item.get("exchCode", "")
        if code and code not in by_exch:
            by_exch[code] = item

    chosen: dict[str, Any] | None = None
    for preferred in _PREFERRED_EXCHCODES:
        if preferred in by_exch:
            chosen = by_exch[preferred]
            break

    if chosen is None:
        chosen = results[0]

    ticker = chosen.get("ticker")
    if not ticker:
        _cache[cache_key] = None
        return None

    exch_code = str(chosen.get("exchCode", ""))
    yf_ticker = _build_yfinance_ticker(str(ticker).upper(), exch_code)
    logger.debug(
        "%s %s resolved to OpenFIGI ticker %s (exchCode=%s) → yfinance ticker %s",
        id_type,
        id_value,
        ticker,
        exch_code,
        yf_ticker,
    )
    _cache[cache_key] = yf_ticker
    return yf_ticker


async def resolve_wkn(wkn: str, api_key: str = "") -> str | None:
    """Return the yfinance ticker for a WKN, or None if it cannot be resolved."""
    wkn = wkn.strip().upper()
    if not wkn:
        return None
    return await _resolve_via_openfigi("ID_WERTPAPIER", wkn, api_key)


async def resolve_isin(isin: str, api_key: str = "") -> str | None:
    """Return the yfinance ticker for an ISIN, or None if it cannot be resolved."""
    isin = isin.strip().upper()
    if not isin:
        return None
    return await _resolve_via_openfigi("ID_ISIN", isin, api_key)
