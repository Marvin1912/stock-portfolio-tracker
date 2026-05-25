"""Tests for TransactionImportService."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.transaction import Transaction
from app.services.portfolio_performance_importer import (
    ParsedTransaction,
    ParseResult,
    SecurityInfo,
    Unit,
)
from app.services.transaction_import_service import TransactionImportService


def _security(uuid: str = "sec-1", ticker: str | None = "CBK.DE") -> SecurityInfo:
    return SecurityInfo(
        uuid=uuid,
        name="Commerzbank AG",
        isin="DE000CBK1001",
        ticker=ticker,
        currency="EUR",
    )


_DEFAULT = object()


def _tx(
    *,
    uuid: str,
    type: str,
    kind: str = "portfolio",
    shares: str = "1",
    amount: str = "100",
    currency: str = "EUR",
    note: str | None = None,
    security: SecurityInfo | None | object = _DEFAULT,
    units: list[Unit] | None = None,
) -> ParsedTransaction:
    sec: SecurityInfo | None = (
        _security() if security is _DEFAULT else security  # type: ignore[assignment]
    )
    return ParsedTransaction(
        kind=kind,  # type: ignore[arg-type]
        uuid=uuid,
        date=datetime(2024, 1, 1),
        type=type,
        amount=Decimal(amount),
        currency=currency,
        shares=Decimal(shares),
        note=note,
        security=sec,
        units=units or [],
    )


def _result(transactions: list[ParsedTransaction]) -> ParseResult:
    return ParseResult(
        version="69",
        base_currency="EUR",
        transactions=transactions,
        securities={t.security.uuid: t.security for t in transactions if t.security},
    )


def _make_db(
    *,
    existing_uuids: set[str] | None = None,
    existing_tickers: dict[str, int] | None = None,
) -> AsyncMock:
    """Mock DB where ``existing_uuids`` come back as already-imported and
    ``existing_tickers`` short-circuit the stock upsert.

    The service issues two query shapes:
      • SELECT Transaction.id WHERE external_uuid = …  — duplicate check
      • SELECT Stock WHERE ticker = …                  — upsert check
    We dispatch by inspecting the compiled SQL.
    """
    existing_uuids = existing_uuids or set()
    existing_tickers = existing_tickers or {}

    inserted_stocks: list[MagicMock] = []

    async def _execute(stmt):  # type: ignore[no-untyped-def]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        result = MagicMock()

        if "external_uuid" in compiled:
            uuid = next(
                (u for u in existing_uuids if u in compiled),
                None,
            )
            result.scalar_one_or_none.return_value = 1 if uuid else None
            return result

        if 'stock"."ticker"' in compiled or "stock.ticker" in compiled:
            ticker = next(
                (t for t in existing_tickers if f"'{t}'" in compiled),
                None,
            )
            if ticker:
                stock = MagicMock()
                stock.id = existing_tickers[ticker]
                stock.ticker = ticker
                result.scalar_one_or_none.return_value = stock
            else:
                result.scalar_one_or_none.return_value = None
            return result

        result.scalar_one_or_none.return_value = None
        return result

    def _add(obj):  # type: ignore[no-untyped-def]
        # Simulate flush assigning an ID to new Stock rows.
        if obj.__class__.__name__ == "Stock":
            obj.id = 100 + len(inserted_stocks)
            inserted_stocks.append(obj)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)
    db.add = MagicMock(side_effect=_add)
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_imports_buy_transaction() -> None:
    db = _make_db()
    result = _result([_tx(uuid="uuid-buy", type="BUY", shares="10", amount="1000")])

    summary = await TransactionImportService().import_xml_result(result, db)

    assert summary.created == 1
    assert summary.skipped_existing == 0
    assert db.add.call_count == 2  # Stock + Transaction


@pytest.mark.asyncio
async def test_skips_duplicate_by_external_uuid() -> None:
    db = _make_db(existing_uuids={"uuid-buy"}, existing_tickers={"CBK.DE": 1})
    result = _result([_tx(uuid="uuid-buy", type="BUY")])

    summary = await TransactionImportService().import_xml_result(result, db)

    assert summary.created == 0
    assert summary.skipped_existing == 1
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_skips_account_side_of_buy_sell() -> None:
    """The account-transaction leg of a BUY has no shares — skip it."""
    db = _make_db(existing_tickers={"CBK.DE": 1})
    result = _result(
        [
            _tx(uuid="port-1", type="BUY", kind="portfolio"),
            _tx(uuid="acct-1", type="BUY", kind="account"),
        ]
    )

    summary = await TransactionImportService().import_xml_result(result, db)

    assert summary.created == 1
    assert summary.skipped_unsupported == 1


@pytest.mark.asyncio
async def test_skips_unsupported_types() -> None:
    db = _make_db(existing_tickers={"CBK.DE": 1})
    result = _result(
        [
            _tx(uuid="dep-1", type="DEPOSIT", kind="account"),
            _tx(uuid="int-1", type="INTEREST", kind="account"),
        ]
    )

    summary = await TransactionImportService().import_xml_result(result, db)

    assert summary.created == 0
    assert summary.skipped_unsupported == 2


@pytest.mark.asyncio
async def test_persists_dividend_with_taxes() -> None:
    db = _make_db(existing_tickers={"CBK.DE": 1})
    units = [Unit(type="TAX", amount=Decimal("3.93"), currency="EUR")]
    result = _result(
        [
            _tx(
                uuid="div-1",
                type="DIVIDENDS",
                kind="account",
                shares="70",
                amount="10.07",
                units=units,
            )
        ]
    )

    summary = await TransactionImportService().import_xml_result(result, db)

    assert summary.created == 1
    added_tx = [
        call.args[0]
        for call in db.add.call_args_list
        if call.args[0].__class__.__name__ == "Transaction"
    ][0]
    assert added_tx.type == "DIVIDEND"
    assert added_tx.tax == Decimal("3.93")


@pytest.mark.asyncio
async def test_reuses_existing_stock() -> None:
    """If a Stock already exists for the security's ticker, do not insert another."""
    db = _make_db(existing_tickers={"CBK.DE": 42})
    result = _result([_tx(uuid="uuid-buy", type="BUY")])

    summary = await TransactionImportService().import_xml_result(result, db)

    assert summary.created == 1
    # Only one .add call → the Transaction; no Stock insert.
    added_types = {call.args[0].__class__.__name__ for call in db.add.call_args_list}
    assert added_types == {"Transaction"}
    added_tx = db.add.call_args_list[0].args[0]
    assert added_tx.stock_id == 42


@pytest.mark.asyncio
async def test_buy_without_security_is_skipped() -> None:
    db = _make_db()
    tx = _tx(uuid="orphan-buy", type="BUY", security=None)
    summary = await TransactionImportService().import_xml_result(_result([tx]), db)

    assert summary.created == 0
    assert summary.skipped_unsupported == 1


@pytest.mark.asyncio
async def test_asset_type_from_security_is_propagated_to_stock() -> None:
    """When SecurityInfo.asset_type is CRYPTO, the new Stock row must reflect it."""
    db = _make_db()
    crypto_sec = SecurityInfo(
        uuid="sec-crypto",
        name="Dogecoin EUR",
        isin=None,
        ticker="DOGE-EUR",
        currency="EUR",
        asset_type="CRYPTO",
    )
    tx = _tx(uuid="buy-doge", type="BUY", security=crypto_sec)

    await TransactionImportService().import_xml_result(_result([tx]), db)

    inserted_stocks = [
        call.args[0]
        for call in db.add.call_args_list
        if call.args[0].__class__.__name__ == "Stock"
    ]
    assert len(inserted_stocks) == 1
    assert inserted_stocks[0].asset_type == "CRYPTO"


# ---------------------------------------------------------------------------
# Cross-source comdirect keying — derive the shared key from the PP note (#113)
# ---------------------------------------------------------------------------


def _added_tx(db: AsyncMock) -> Transaction:
    tx = next(
        call.args[0]
        for call in db.add.call_args_list
        if call.args[0].__class__.__name__ == "Transaction"
    )
    return cast(Transaction, tx)


@pytest.mark.asyncio
async def test_comdirect_note_yields_shared_key() -> None:
    """A comdirect PP note is keyed by the shared pdf:comdirect:{ref} key,
    not PP's random uuid."""
    db = _make_db(existing_tickers={"CBK.DE": 1})
    note = "Ord.-Nr.: 072324316214-001 | R.-Nr.: 9988776655"
    result = _result([_tx(uuid="pp-random-uuid", type="BUY", note=note)])

    summary = await TransactionImportService().import_xml_result(result, db)

    assert summary.created == 1
    assert _added_tx(db).external_uuid == "pdf:comdirect:072324316214-001"


@pytest.mark.asyncio
async def test_xml_skipped_when_pdf_already_imported() -> None:
    """An XML row imported after the matching PDF dedupes via the shared key."""
    db = _make_db(
        existing_uuids={"pdf:comdirect:072324316214-001"},
        existing_tickers={"CBK.DE": 1},
    )
    note = "Ord.-Nr.: 072324316214-001 | R.-Nr.: 9988776655"
    result = _result([_tx(uuid="pp-random-uuid", type="BUY", note=note)])

    summary = await TransactionImportService().import_xml_result(result, db)

    assert summary.created == 0
    assert summary.skipped_existing == 1
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_duplicate_within_same_batch_is_skipped() -> None:
    """Two rows in one XML sharing a comdirect key must not both insert.

    The session is autoflush=False, so the DB duplicate check cannot see a row
    added earlier in the same run. Without batch-local dedup both rows reach the
    flush and collide on uq_transaction_external_uuid.
    """
    db = _make_db(existing_tickers={"CBK.DE": 1})
    note = "Ord.-Nr.: 000286017243-001 | R.-Nr.: 1234567890"
    result = _result(
        [
            _tx(uuid="pp-a", type="BUY", note=note),
            _tx(uuid="pp-b", type="BUY", note=note),
        ]
    )

    summary = await TransactionImportService().import_xml_result(result, db)

    assert summary.created == 1
    assert summary.skipped_existing == 1
    assert _added_tx(db).external_uuid == "pdf:comdirect:000286017243-001"


@pytest.mark.asyncio
async def test_non_comdirect_note_falls_back_to_pp_uuid() -> None:
    db = _make_db(existing_tickers={"CBK.DE": 1})
    result = _result(
        [_tx(uuid="pp-uuid-x", type="BUY", note="Purchase via Sparplan")]
    )

    summary = await TransactionImportService().import_xml_result(result, db)

    assert summary.created == 1
    assert _added_tx(db).external_uuid == "pp-uuid-x"


@pytest.mark.asyncio
async def test_legacy_order_form_falls_back_to_pp_uuid() -> None:
    """The legacy ' / '-separated Order-Nr. form has no PDF counterpart."""
    db = _make_db(existing_tickers={"CBK.DE": 1})
    result = _result(
        [_tx(uuid="pp-uuid-y", type="BUY", note="Order-Nr.: 71871368321 / 001")]
    )

    summary = await TransactionImportService().import_xml_result(result, db)

    assert summary.created == 1
    assert _added_tx(db).external_uuid == "pp-uuid-y"


# ---------------------------------------------------------------------------
# Idempotency on re-import — service-level integration with the parser
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reimport_is_idempotent() -> None:
    """Running the importer twice on the same XML inserts each row only once."""
    from app.services.portfolio_performance_importer import PortfolioPerformanceImporter
    from tests.test_portfolio_performance_importer import SAMPLE_XML

    parsed = PortfolioPerformanceImporter().parse_bytes(SAMPLE_XML.encode("utf-8"))

    seen_uuids: set[str] = set()

    async def _execute(stmt):  # type: ignore[no-untyped-def]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        result = MagicMock()
        if "external_uuid" in compiled:
            found = next((u for u in seen_uuids if u in compiled), None)
            result.scalar_one_or_none.return_value = 1 if found else None
        else:
            result.scalar_one_or_none.return_value = None
        return result

    inserted: list = []

    def _add(obj):  # type: ignore[no-untyped-def]
        if obj.__class__.__name__ == "Stock":
            obj.id = 100 + len(inserted)
        if obj.__class__.__name__ == "Transaction":
            seen_uuids.add(obj.external_uuid)
        inserted.append(obj)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)
    db.add = MagicMock(side_effect=_add)
    db.flush = AsyncMock()

    first = await TransactionImportService().import_xml_result(parsed, db)
    second = await TransactionImportService().import_xml_result(parsed, db)

    assert first.created >= 1
    assert second.created == 0
    assert second.skipped_existing == first.created
