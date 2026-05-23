"""Resolve each unique XML security to a yfinance-valid ticker.

The Portfolio Performance XML carries ticker strings that Yahoo Finance
often cannot resolve directly:

* German exchange suffixes such as ``.TG`` (Tradegate) — Yahoo uses
  ``.DE``/``.SG``/``.F``.
* Cryptocurrencies as bare symbols (``DOGE``, ``IOTA``) — Yahoo needs
  ``DOGE-EUR``/``IOTA-EUR``.

This module probes Yahoo Finance for every unique ``SecurityInfo`` and
returns a ``ResolvedSecurity`` whose ``status`` is either ``valid`` or
``needs_attention``.  The router then drives the preview UI from that.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Literal

from app.models.stock import ASSET_TYPE_CRYPTO, ASSET_TYPE_STOCK
from app.services.openfigi_lookup import resolve_isin
from app.services.portfolio_performance_importer import SecurityInfo
from app.services.stock_lookup import StockInfo, fetch_stock_info

Status = Literal["valid", "needs_attention"]
Source = Literal["xml", "openfigi", "crypto_pair", "manual"]

_CRYPTO_QUOTES: tuple[str, ...] = ("EUR", "USD")
_CRYPTO_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,6}$")
_MAX_CONCURRENT = 8


def _asset_type_from_quote(qt: str | None) -> str:
    return ASSET_TYPE_CRYPTO if (qt or "").upper() == "CRYPTOCURRENCY" else ASSET_TYPE_STOCK


def crypto_symbol_stem(ticker: str | None) -> str:
    """Reduce a ticker to its likely base crypto symbol.

    ``BTC`` → ``BTC``; ``BTC.DE`` / ``BTC.TG`` → ``BTC``;
    ``BTC-EUR`` / ``BTC-USD`` → ``BTC``.
    """
    t = (ticker or "").strip().upper()
    if not t:
        return ""
    if "." in t:
        t = t.split(".", 1)[0]
    if "-" in t:
        head, tail = t.rsplit("-", 1)
        if tail in _CRYPTO_QUOTES:
            t = head
    return t


async def find_crypto_pair(
    ticker: str | None, *, require_crypto: bool = False
) -> tuple[str, StockInfo] | None:
    """Probe Yahoo for a ``<SYM>-EUR``/``<SYM>-USD`` pair derived from *ticker*.

    Returns the first hit as ``(pair_ticker, StockInfo)``. With
    ``require_crypto=True`` only Yahoo entries whose ``quoteType`` is
    ``CRYPTOCURRENCY`` are accepted — used by the manual STOCK→CRYPTO toggle
    to avoid labelling an equity at e.g. ``BTC-EUR`` as crypto.
    """
    stem = crypto_symbol_stem(ticker)
    if not stem or not _CRYPTO_SYMBOL_RE.match(stem):
        return None

    candidates: list[str] = []
    raw = (ticker or "").strip().upper()
    if "-" in raw and raw.rsplit("-", 1)[1] in _CRYPTO_QUOTES:
        candidates.append(raw)
    for quote in _CRYPTO_QUOTES:
        candidate = f"{stem}-{quote}"
        if candidate not in candidates:
            candidates.append(candidate)

    for candidate in candidates:
        info = await fetch_stock_info(candidate)
        if info is None:
            continue
        if require_crypto and (info.quote_type or "").upper() != "CRYPTOCURRENCY":
            continue
        return candidate, info
    return None


@dataclass(slots=True)
class ResolvedSecurity:
    uuid: str
    original_ticker: str | None
    original_name: str | None
    isin: str | None
    status: Status
    resolved_ticker: str | None
    asset_type: str  # ASSET_TYPE_STOCK | ASSET_TYPE_CRYPTO
    suggestion_source: Source
    yahoo_name: str | None
    currency: str | None

    @property
    def display(self) -> str:
        parts = [p for p in (self.original_name, self.original_ticker, self.isin) if p]
        return " · ".join(parts) if parts else self.uuid


async def resolve_securities(
    securities: list[SecurityInfo], *, openfigi_api_key: str = ""
) -> dict[str, ResolvedSecurity]:
    """Resolve every security concurrently. Keyed by UUID."""
    sem = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _one(sec: SecurityInfo) -> tuple[str, ResolvedSecurity]:
        async with sem:
            return sec.uuid, await resolve_security(sec, openfigi_api_key=openfigi_api_key)

    pairs = await asyncio.gather(*(_one(s) for s in securities))
    return dict(pairs)


async def resolve_security(
    security: SecurityInfo, *, openfigi_api_key: str = ""
) -> ResolvedSecurity:
    raw_ticker = (security.ticker or "").strip().upper() or None

    # 1) Try the raw XML ticker as-is.
    if raw_ticker:
        info = await fetch_stock_info(raw_ticker)
        if info:
            return _valid(
                security,
                resolved_ticker=raw_ticker,
                asset_type=_asset_type_from_quote(info.quote_type),
                source="xml",
                yahoo_name=info.name,
                currency=info.currency,
            )

    # 2) ISIN → OpenFIGI → ticker.
    if security.isin:
        candidate = await resolve_isin(security.isin, api_key=openfigi_api_key)
        if candidate:
            info = await fetch_stock_info(candidate)
            if info:
                return _valid(
                    security,
                    resolved_ticker=candidate.upper(),
                    asset_type=_asset_type_from_quote(info.quote_type),
                    source="openfigi",
                    yahoo_name=info.name,
                    currency=info.currency,
                )

    # 3) Crypto heuristic — short symbol, no ISIN.
    if raw_ticker and not security.isin:
        pair = await find_crypto_pair(raw_ticker)
        if pair is not None:
            pair_ticker, info = pair
            return _valid(
                security,
                resolved_ticker=pair_ticker,
                asset_type=_asset_type_from_quote(info.quote_type),
                source="crypto_pair",
                yahoo_name=info.name,
                currency=info.currency,
            )

    return ResolvedSecurity(
        uuid=security.uuid,
        original_ticker=raw_ticker,
        original_name=security.name,
        isin=security.isin,
        status="needs_attention",
        resolved_ticker=None,
        asset_type=ASSET_TYPE_STOCK,
        suggestion_source="manual",
        yahoo_name=None,
        currency=security.currency,
    )


def _valid(
    security: SecurityInfo,
    *,
    resolved_ticker: str,
    asset_type: str,
    source: Source,
    yahoo_name: str,
    currency: str | None,
) -> ResolvedSecurity:
    return ResolvedSecurity(
        uuid=security.uuid,
        original_ticker=(security.ticker or "").strip().upper() or None,
        original_name=security.name,
        isin=security.isin,
        status="valid",
        resolved_ticker=resolved_ticker,
        asset_type=asset_type,
        suggestion_source=source,
        yahoo_name=yahoo_name,
        currency=currency or security.currency,
    )
