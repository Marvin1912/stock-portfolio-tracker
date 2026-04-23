"""HTMX fragment endpoints for the portfolio UI."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_async_session
from app.models.holding import Holding
from app.models.stock import ASSET_TYPE_CRYPTO, ASSET_TYPE_STOCK, Stock
from app.services.fx_service import to_eur
from app.services.openfigi_lookup import resolve_wkn
from app.services.stock_lookup import fetch_stock_info

_SUPPORTED_QUOTES = ("EUR", "USD")


def _build_crypto_ticker(symbol: str, quote: str) -> str:
    return f"{symbol.strip().upper()}-{quote.strip().upper()}"

router = APIRouter(prefix="/htmx", tags=["htmx"])

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_DB = Depends(get_async_session)


def _render(request: Request, name: str, context: dict) -> HTMLResponse:  # type: ignore[type-arg]
    context["request"] = request
    return templates.TemplateResponse(request=request, name=name, context=context)


# ---------------------------------------------------------------------------
# Ticker validation (inline, triggered on input)
# ---------------------------------------------------------------------------


@router.get("/validate-ticker", response_class=HTMLResponse)
async def validate_ticker(
    request: Request,
    ticker: str = "",
    db: AsyncSession = _DB,
) -> HTMLResponse:
    """Return an inline validation hint for the ticker field."""
    ticker = ticker.strip().upper()
    if not ticker:
        return HTMLResponse("")

    # Check DB first (fast path)
    result = await db.execute(select(Stock).where(Stock.ticker == ticker))
    stock = result.scalar_one_or_none()
    if stock:
        return _render(request, "partials/ticker_hint.html", {"valid": True, "name": stock.name})

    # Fallback to yfinance
    info = await fetch_stock_info(ticker)
    if info:
        return _render(request, "partials/ticker_hint.html", {"valid": True, "name": info.name})

    return _render(request, "partials/ticker_hint.html", {"valid": False, "name": None})


# ---------------------------------------------------------------------------
# WKN validation (inline, triggered on input)
# ---------------------------------------------------------------------------


@router.get("/validate-wkn", response_class=HTMLResponse)
async def validate_wkn(
    request: Request,
    wkn: str = "",
) -> HTMLResponse:
    """Return an inline validation hint for the WKN field."""
    wkn = wkn.strip().upper()
    if not wkn:
        return HTMLResponse("")

    settings = get_settings()
    ticker = await resolve_wkn(wkn, api_key=settings.openfigi_api_key)
    if ticker is None:
        return _render(request, "partials/ticker_hint.html", {"valid": False, "name": None})

    info = await fetch_stock_info(ticker)
    if info:
        return _render(request, "partials/ticker_hint.html", {"valid": True, "name": info.name})

    return _render(request, "partials/ticker_hint.html", {"valid": False, "name": None})


# ---------------------------------------------------------------------------
# Add holding form & submission
# ---------------------------------------------------------------------------


@router.get("/holdings/add-form", response_class=HTMLResponse)
async def add_holding_form(request: Request) -> HTMLResponse:
    return _render(request, "partials/add_holding_form.html", {})


@router.post("/holdings", response_class=HTMLResponse)
async def htmx_create_holding(
    request: Request,
    ticker: str = Form(""),
    wkn: str = Form(""),
    quantity: str = Form(...),
    db: AsyncSession = _DB,
) -> HTMLResponse:
    ticker = ticker.strip().upper()
    wkn = wkn.strip().upper()

    # Validate mutual exclusion
    if ticker and wkn:
        return _render(
            request,
            "partials/add_holding_form.html",
            {"error": "Please provide either a Ticker or a WKN, not both.", "quantity": quantity},
        )
    if not ticker and not wkn:
        return _render(
            request,
            "partials/add_holding_form.html",
            {"error": "Please provide a Ticker or a WKN.", "quantity": quantity},
        )

    # Resolve WKN → ticker when WKN is provided
    if wkn:
        settings = get_settings()
        resolved = await resolve_wkn(wkn, api_key=settings.openfigi_api_key)
        if resolved is None:
            return _render(
                request,
                "partials/add_holding_form.html",
                {"error": f"WKN '{wkn}' could not be resolved.", "wkn": wkn, "quantity": quantity},
            )
        ticker = resolved

    try:
        qty = Decimal(quantity)
        if qty <= 0:
            raise ValueError
    except (ValueError, Exception):
        return _render(
            request,
            "partials/add_holding_form.html",
            {
                "error": "Quantity must be a positive number.",
                "ticker": ticker if not wkn else "",
                "wkn": wkn,
                "quantity": quantity,
            },
        )

    return await _create_or_attach_holding(
        request=request,
        db=db,
        ticker=ticker,
        qty=qty,
        asset_type=ASSET_TYPE_STOCK,
        not_found_form="partials/add_holding_form.html",
        not_found_context={"ticker": ticker, "quantity": quantity},
    )


async def _create_or_attach_holding(
    *,
    request: Request,
    db: AsyncSession,
    ticker: str,
    qty: Decimal,
    asset_type: str,
    not_found_form: str,
    not_found_context: dict[str, object],
) -> HTMLResponse:
    """Find or create a Stock, attach a Holding, and return the OOB row HTML."""
    result = await db.execute(select(Stock).where(Stock.ticker == ticker))
    stock = result.scalar_one_or_none()

    if stock is None:
        info = await fetch_stock_info(ticker)
        if info is None:
            return _render(
                request,
                not_found_form,
                {"error": f"Ticker '{ticker}' not found.", **not_found_context},
            )
        stock = Stock(
            ticker=info.ticker,
            name=info.name,
            currency=info.currency,
            asset_type=asset_type,
            current_price=info.current_price,
        )
        db.add(stock)
        await db.flush()

    holding = Holding(stock_id=stock.id, quantity=qty)
    db.add(holding)
    await db.flush()
    await db.refresh(holding)

    current_value = (
        qty * to_eur(stock.current_price, stock.currency)
        if stock.current_price is not None
        else None
    )
    row_resp = _render(
        request,
        "partials/holding_row.html",
        {
            "holding": {
                "id": holding.id,
                "ticker": stock.ticker,
                "name": stock.name,
                "asset_type": stock.asset_type,
                "quantity": qty,
                "current_value": current_value,
            }
        },
    )
    row_html = bytes(row_resp.body).decode()
    oob_attr = 'class="anim-flash" hx-swap-oob="beforeend:#holdings-tbody"'
    oob_html = (
        row_html.replace(
            f'<tr id="holding-row-{holding.id}">',
            f'<tr id="holding-row-{holding.id}" {oob_attr}>',
        )
        + "\n<script>"
        "var s=document.getElementById('add-form-slot');"
        "s.classList.remove('open');"
        "setTimeout(function(){s.innerHTML='';},300);"
        "</script>"
    )
    return HTMLResponse(oob_html)


# ---------------------------------------------------------------------------
# Holding row (used for cancel/restore)
# ---------------------------------------------------------------------------


@router.get("/holdings/{holding_id}/row", response_class=HTMLResponse)
async def holding_row(
    request: Request,
    holding_id: int,
    db: AsyncSession = _DB,
) -> HTMLResponse:
    holding = await db.get(Holding, holding_id)
    if holding is None:
        return HTMLResponse("", status_code=404)
    stock = holding.stock
    current_value = (
        holding.quantity * to_eur(stock.current_price, stock.currency)
        if stock.current_price is not None
        else None
    )
    return _render(
        request,
        "partials/holding_row.html",
        {
            "holding": {
                "id": holding.id,
                "ticker": stock.ticker,
                "name": stock.name,
                "asset_type": stock.asset_type,
                "quantity": holding.quantity,
                "current_value": current_value,
            }
        },
    )


# ---------------------------------------------------------------------------
# Edit holding form & submission
# ---------------------------------------------------------------------------


@router.get("/holdings/{holding_id}/edit-form", response_class=HTMLResponse)
async def edit_holding_form(
    request: Request,
    holding_id: int,
    db: AsyncSession = _DB,
) -> HTMLResponse:
    result = await db.execute(
        select(Holding)
        .options(selectinload(Holding.stock))
        .where(Holding.id == holding_id)
    )
    holding = result.scalar_one_or_none()
    if holding is None:
        return HTMLResponse("Not found", status_code=404)
    stock = holding.stock
    return _render(
        request,
        "partials/edit_holding_form.html",
        {
            "holding": {
                "id": holding.id,
                "ticker": stock.ticker,
                "name": stock.name,
                "quantity": holding.quantity,
            }
        },
    )


@router.put("/holdings/{holding_id}", response_class=HTMLResponse)
async def htmx_update_holding(
    request: Request,
    holding_id: int,
    quantity: str = Form(...),
    db: AsyncSession = _DB,
) -> HTMLResponse:
    holding = await db.get(Holding, holding_id)
    if holding is None:
        return HTMLResponse("Not found", status_code=404)

    try:
        qty = Decimal(quantity)
        if qty <= 0:
            raise ValueError
    except (ValueError, Exception):
        stock = holding.stock
        return _render(
            request,
            "partials/edit_holding_form.html",
            {
                "error": "Quantity must be a positive number.",
                "holding": {
                    "id": holding.id,
                    "ticker": stock.ticker,
                    "name": stock.name,
                    "quantity": holding.quantity,
                },
            },
        )

    holding.quantity = qty
    await db.flush()
    await db.refresh(holding)
    stock = holding.stock
    current_value = (
        qty * to_eur(stock.current_price, stock.currency)
        if stock.current_price is not None
        else None
    )

    return _render(
        request,
        "partials/holding_row.html",
        {
            "holding": {
                "id": holding.id,
                "ticker": stock.ticker,
                "name": stock.name,
                "asset_type": stock.asset_type,
                "quantity": qty,
                "current_value": current_value,
            }
        },
    )


# ---------------------------------------------------------------------------
# Add crypto holding
# ---------------------------------------------------------------------------


@router.get("/validate-crypto", response_class=HTMLResponse)
async def validate_crypto(
    request: Request,
    symbol: str = "",
    quote: str = "EUR",
    db: AsyncSession = _DB,
) -> HTMLResponse:
    """Return an inline validation hint for the crypto symbol field."""
    symbol = symbol.strip().upper()
    quote = quote.strip().upper() or "EUR"
    if not symbol:
        return HTMLResponse("")
    if quote not in _SUPPORTED_QUOTES:
        return _render(request, "partials/ticker_hint.html", {"valid": False, "name": None})

    ticker = _build_crypto_ticker(symbol, quote)

    result = await db.execute(select(Stock).where(Stock.ticker == ticker))
    stock = result.scalar_one_or_none()
    if stock:
        return _render(request, "partials/ticker_hint.html", {"valid": True, "name": stock.name})

    info = await fetch_stock_info(ticker)
    if info:
        return _render(request, "partials/ticker_hint.html", {"valid": True, "name": info.name})

    return _render(request, "partials/ticker_hint.html", {"valid": False, "name": None})


@router.get("/holdings/add-crypto-form", response_class=HTMLResponse)
async def add_crypto_form(request: Request) -> HTMLResponse:
    return _render(
        request,
        "partials/add_crypto_form.html",
        {"supported_quotes": _SUPPORTED_QUOTES},
    )


@router.post("/crypto-holdings", response_class=HTMLResponse)
async def htmx_create_crypto_holding(
    request: Request,
    symbol: str = Form(""),
    quote: str = Form("EUR"),
    quantity: str = Form(...),
    db: AsyncSession = _DB,
) -> HTMLResponse:
    symbol = symbol.strip().upper()
    quote = quote.strip().upper() or "EUR"

    if not symbol:
        return _render(
            request,
            "partials/add_crypto_form.html",
            {
                "error": "Please provide a crypto symbol (e.g. BTC).",
                "quote": quote,
                "quantity": quantity,
                "supported_quotes": _SUPPORTED_QUOTES,
            },
        )
    if quote not in _SUPPORTED_QUOTES:
        return _render(
            request,
            "partials/add_crypto_form.html",
            {
                "error": f"Quote currency must be one of: {', '.join(_SUPPORTED_QUOTES)}.",
                "symbol": symbol,
                "quantity": quantity,
                "supported_quotes": _SUPPORTED_QUOTES,
            },
        )

    try:
        qty = Decimal(quantity)
        if qty <= 0:
            raise ValueError
    except (ValueError, Exception):
        return _render(
            request,
            "partials/add_crypto_form.html",
            {
                "error": "Quantity must be a positive number.",
                "symbol": symbol,
                "quote": quote,
                "quantity": quantity,
                "supported_quotes": _SUPPORTED_QUOTES,
            },
        )

    ticker = _build_crypto_ticker(symbol, quote)
    return await _create_or_attach_holding(
        request=request,
        db=db,
        ticker=ticker,
        qty=qty,
        asset_type=ASSET_TYPE_CRYPTO,
        not_found_form="partials/add_crypto_form.html",
        not_found_context={
            "symbol": symbol,
            "quote": quote,
            "quantity": quantity,
            "supported_quotes": _SUPPORTED_QUOTES,
        },
    )


# ---------------------------------------------------------------------------
# Delete holding
# ---------------------------------------------------------------------------


@router.delete("/holdings/{holding_id}", response_class=HTMLResponse)
async def htmx_delete_holding(
    holding_id: int,
    db: AsyncSession = _DB,
) -> HTMLResponse:
    holding = await db.get(Holding, holding_id)
    if holding is None:
        return HTMLResponse("", status_code=200)  # already gone — just remove the row
    await db.delete(holding)
    return HTMLResponse("")  # empty response → HTMX removes the row
