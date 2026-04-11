"""HTMX fragment endpoints for the portfolio UI."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_async_session
from app.models.holding import Holding
from app.models.stock import Stock
from app.services.stock_lookup import fetch_stock_info

router = APIRouter(prefix="/htmx", tags=["htmx"])

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_DB = Depends(get_async_session)

_WKN_RE = re.compile(r'^[A-Za-z0-9]{6}$')


def _render(request: Request, name: str, context: dict) -> HTMLResponse:  # type: ignore[type-arg]
    context["request"] = request
    return templates.TemplateResponse(request=request, name=name, context=context)


# ---------------------------------------------------------------------------
# WKN validation (inline, triggered on input)
# ---------------------------------------------------------------------------


@router.get("/validate-wkn", response_class=HTMLResponse)
async def validate_wkn(
    request: Request,
    wkn: str = "",
    ticker: str = "",
    db: AsyncSession = _DB,
) -> HTMLResponse:
    """Return an inline validation hint for the WKN field."""
    wkn = wkn.strip().upper()
    if not wkn:
        return HTMLResponse("")

    # Validate WKN format (exactly 6 alphanumeric characters)
    if not _WKN_RE.match(wkn):
        return _render(request, "partials/wkn_hint.html", {"valid": False, "name": None})

    # Check DB first (fast path)
    result = await db.execute(select(Stock).where(Stock.wkn == wkn))
    stock = result.scalar_one_or_none()
    if stock:
        return _render(request, "partials/wkn_hint.html", {"valid": True, "name": stock.name})

    # If not in DB, validate via yfinance using the provided ticker
    ticker = ticker.strip().upper()
    if ticker:
        info = await fetch_stock_info(ticker)
        if info:
            return _render(request, "partials/wkn_hint.html", {"valid": True, "name": info.name})

    return _render(request, "partials/wkn_hint.html", {"valid": False, "name": None})


# ---------------------------------------------------------------------------
# Add holding form & submission
# ---------------------------------------------------------------------------


@router.get("/holdings/add-form", response_class=HTMLResponse)
async def add_holding_form(request: Request) -> HTMLResponse:
    return _render(request, "partials/add_holding_form.html", {})


@router.post("/holdings", response_class=HTMLResponse)
async def htmx_create_holding(
    request: Request,
    wkn: str = Form(...),
    ticker: str = Form(...),
    quantity: str = Form(...),
    db: AsyncSession = _DB,
) -> HTMLResponse:
    wkn = wkn.strip().upper()
    ticker = ticker.strip().upper()

    if not _WKN_RE.match(wkn):
        return _render(
            request,
            "partials/add_holding_form.html",
            {
                "error": "WKN must be exactly 6 alphanumeric characters.",
                "wkn": wkn,
                "ticker": ticker,
                "quantity": quantity,
            },
        )

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
                "wkn": wkn,
                "ticker": ticker,
                "quantity": quantity,
            },
        )

    # Resolve or create the stock
    result = await db.execute(select(Stock).where(Stock.wkn == wkn))
    stock = result.scalar_one_or_none()

    if stock is None:
        info = await fetch_stock_info(ticker)
        if info is None:
            return _render(
                request,
                "partials/add_holding_form.html",
                {
                    "error": f"Ticker '{ticker}' not found.",
                    "wkn": wkn,
                    "ticker": ticker,
                    "quantity": quantity,
                },
            )
        stock = Stock(
            wkn=wkn,
            ticker=info.ticker,
            name=info.name,
            currency=info.currency,
            current_price=info.current_price,
        )
        db.add(stock)
        await db.flush()

    holding = Holding(stock_id=stock.id, quantity=qty)
    db.add(holding)
    await db.flush()
    await db.refresh(holding)

    current_value = qty * stock.current_price if stock.current_price is not None else None
    return _render(
        request,
        "partials/holding_row.html",
        {
            "holding": {
                "id": holding.id,
                "wkn": stock.wkn,
                "name": stock.name,
                "currency": stock.currency,
                "quantity": qty,
                "current_value": current_value,
            }
        },
    )


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
        holding.quantity * stock.current_price if stock.current_price is not None else None
    )
    return _render(
        request,
        "partials/holding_row.html",
        {
            "holding": {
                "id": holding.id,
                "wkn": stock.wkn,
                "name": stock.name,
                "currency": stock.currency,
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
    holding = await db.get(Holding, holding_id)
    if holding is None:
        return HTMLResponse("Not found", status_code=404)
    stock = holding.stock
    return _render(
        request,
        "partials/edit_holding_form.html",
        {
            "holding": {
                "id": holding.id,
                "wkn": stock.wkn,
                "name": stock.name,
                "currency": stock.currency,
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
                    "wkn": stock.wkn,
                    "name": stock.name,
                    "currency": stock.currency,
                    "quantity": holding.quantity,
                },
            },
        )

    holding.quantity = qty
    await db.flush()
    await db.refresh(holding)
    stock = holding.stock
    current_value = qty * stock.current_price if stock.current_price is not None else None

    return _render(
        request,
        "partials/holding_row.html",
        {
            "holding": {
                "id": holding.id,
                "wkn": stock.wkn,
                "name": stock.name,
                "currency": stock.currency,
                "quantity": qty,
                "current_value": current_value,
            }
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
