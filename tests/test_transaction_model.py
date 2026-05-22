"""Schema-level checks for the Transaction model."""

from __future__ import annotations

from app.models.transaction import (
    TX_SOURCES,
    TX_TYPE_BUY,
    TX_TYPE_DIVIDEND,
    TX_TYPE_FEE,
    TX_TYPE_SELL,
    TX_TYPE_TAX,
    TX_TYPE_TRANSFER_IN,
    TX_TYPE_TRANSFER_OUT,
    TX_TYPES,
    Transaction,
)


def test_transaction_table_columns_and_indexes() -> None:
    table = Transaction.__table__

    assert table.schema == "finance"
    assert table.name == "transaction"

    expected = {
        "id",
        "external_uuid",
        "stock_id",
        "date",
        "type",
        "shares",
        "amount",
        "currency",
        "fee",
        "tax",
        "note",
        "source",
        "created_at",
    }
    assert set(table.columns.keys()) == expected

    assert table.c.stock_id.nullable is True
    assert table.c.external_uuid.nullable is True
    assert table.c.note.nullable is True
    assert table.c.date.nullable is False

    fk = next(iter(table.c.stock_id.foreign_keys))
    assert fk.column.table.name == "stock"
    assert fk.ondelete == "CASCADE"

    uq_names = {uc.name for uc in table.constraints if uc.__class__.__name__ == "UniqueConstraint"}
    assert "uq_transaction_external_uuid" in uq_names

    idx_names = {idx.name for idx in table.indexes}
    assert "ix_transaction_stock_id_date" in idx_names


def test_transaction_type_and_source_constants() -> None:
    assert set(TX_TYPES) == {
        TX_TYPE_BUY,
        TX_TYPE_SELL,
        TX_TYPE_DIVIDEND,
        TX_TYPE_FEE,
        TX_TYPE_TAX,
        TX_TYPE_TRANSFER_IN,
        TX_TYPE_TRANSFER_OUT,
    }
    assert set(TX_SOURCES) == {"XML", "PDF", "MANUAL"}
