"""Tests for the Portfolio Performance XML importer/parser."""

from __future__ import annotations

import io
import zipfile
from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.services.portfolio_performance_importer import PortfolioPerformanceImporter

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<client>
  <version>69</version>
  <baseCurrency>EUR</baseCurrency>
  <securities>
    <security>
      <uuid>2bab2728-f3b2-4ca0-8330-825446831ade</uuid>
      <name>Commerzbank AG</name>
      <currencyCode>EUR</currencyCode>
      <isin>DE000CBK1001</isin>
      <tickerSymbol>CBK.DE</tickerSymbol>
    </security>
    <security>
      <uuid>11111111-2222-3333-4444-555555555555</uuid>
      <name>SAP SE</name>
      <currencyCode>EUR</currencyCode>
      <isin>DE0007164600</isin>
      <tickerSymbol>SAP.DE</tickerSymbol>
    </security>
  </securities>
  <accounts>
    <account>
      <uuid>6908edd0-1bb5-411e-bb4d-678a858eff66</uuid>
      <name>Cash Account</name>
      <currencyCode>EUR</currencyCode>
      <transactions>
        <account-transaction>
          <uuid>11d2ebf5-a0c5-4de1-b2f6-19a0a701e1f6</uuid>
          <date>2019-05-27T00:00</date>
          <currencyCode>EUR</currencyCode>
          <amount>1007</amount>
          <security reference="../../../../../securities/security"/>
          <shares>7000000000</shares>
          <note>Dividend Payment</note>
          <units>
            <unit type="TAX">
              <amount currency="EUR" amount="393"/>
            </unit>
          </units>
          <type>DIVIDENDS</type>
        </account-transaction>
      </transactions>
    </account>
  </accounts>
  <portfolios>
    <portfolio>
      <uuid>404c0fbf-68ab-4925-bdcb-9838e2380317</uuid>
      <name>My Portfolio</name>
      <transactions>
        <portfolio-transaction>
          <uuid>f73225c5-c9ee-4f3a-8b83-c1e883f2b437</uuid>
          <date>2021-08-02T00:00</date>
          <currencyCode>EUR</currencyCode>
          <amount>10000</amount>
          <security reference="../../../../../securities/security"/>
          <shares>9000000</shares>
          <note>Purchase via Sparplan</note>
          <units>
            <unit type="FEE">
              <amount currency="EUR" amount="1000"/>
            </unit>
            <unit type="TAX">
              <amount currency="EUR" amount="100"/>
            </unit>
          </units>
          <type>BUY</type>
        </portfolio-transaction>
        <portfolio-transaction>
          <uuid>abcdef01-0000-0000-0000-000000000001</uuid>
          <date>2022-03-10T00:00</date>
          <currencyCode>EUR</currencyCode>
          <amount>5000000</amount>
          <security reference="../../../../../securities/security[2]"/>
          <shares>3000000</shares>
          <type>SELL</type>
        </portfolio-transaction>
      </transactions>
    </portfolio>
  </portfolios>
</client>
"""


def _parse(xml: str = SAMPLE_XML):
    return PortfolioPerformanceImporter().parse_bytes(xml.encode("utf-8"))


def test_parser_extracts_basic_metadata() -> None:
    result = _parse()

    assert result.version == "69"
    assert result.base_currency == "EUR"
    assert len(result.securities) == 2


def test_parser_decodes_millionth_values() -> None:
    result = _parse()

    portfolio_tx = next(t for t in result.transactions if t.type == "BUY")
    # shares: 9_000_000 / 1_000_000 = 9
    assert portfolio_tx.shares == Decimal("9.000000")
    # amount: 10_000 / 1_000_000 = 0.01
    assert portfolio_tx.amount == Decimal("0.010000")


def test_parser_resolves_security_references() -> None:
    result = _parse()

    buy = next(t for t in result.transactions if t.type == "BUY")
    sell = next(t for t in result.transactions if t.type == "SELL")

    assert buy.security is not None
    assert buy.security.ticker == "CBK.DE"
    assert sell.security is not None
    assert sell.security.ticker == "SAP.DE"


def test_parser_extracts_units_fees_and_taxes() -> None:
    result = _parse()

    buy = next(t for t in result.transactions if t.type == "BUY")
    assert buy.fees == Decimal("0.001000")
    assert buy.taxes == Decimal("0.000100")


def test_parser_includes_portfolio_and_account_transactions() -> None:
    result = _parse()

    kinds = {t.kind for t in result.transactions}
    assert kinds == {"portfolio", "account"}

    div = next(t for t in result.transactions if t.type == "DIVIDENDS")
    assert div.kind == "account"
    assert div.security is not None
    assert div.security.ticker == "CBK.DE"


def test_parser_sorts_transactions_by_date() -> None:
    result = _parse()
    dates = [t.date for t in result.transactions]
    assert dates == sorted(dates)


def test_parser_handles_zip_archive() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("client.xml", SAMPLE_XML)

    result = PortfolioPerformanceImporter().parse_bytes(buf.getvalue())
    assert result.total_count == 3


def test_parser_summary_helpers() -> None:
    result = _parse()

    breakdown = {b.type: b.count for b in result.type_breakdown}
    assert breakdown == {"BUY": 1, "SELL": 1, "DIVIDENDS": 1}
    assert result.total_count == 3
    assert len(result.unique_securities) == 2


# ---------------------------------------------------------------------------
# Router integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_xml_get_renders_upload_form(client: AsyncClient) -> None:
    response = await client.get("/import/xml")
    assert response.status_code == 200
    assert "Upload Portfolio Performance file" in response.text


@pytest.mark.asyncio
async def test_import_xml_post_rejects_non_xml(client: AsyncClient) -> None:
    response = await client.post(
        "/import/xml",
        files={"file": ("report.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 200
    assert ".xml or .zip" in response.text


@pytest.mark.asyncio
async def test_import_xml_post_rejects_invalid_xml(client: AsyncClient) -> None:
    response = await client.post(
        "/import/xml",
        files={"file": ("bad.xml", b"<not-xml", "application/xml")},
    )
    assert response.status_code == 200
    assert "Invalid XML" in response.text


def test_parser_finds_transactions_nested_in_crossentry() -> None:
    """BUY portfolio-transactions are serialised inside account crossEntry in real PP files."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<client>
  <version>69</version>
  <baseCurrency>EUR</baseCurrency>
  <securities>
    <security>
      <uuid>sec-1</uuid>
      <name>Commerzbank AG</name>
      <currencyCode>EUR</currencyCode>
      <tickerSymbol>CBK.DE</tickerSymbol>
    </security>
  </securities>
  <accounts>
    <account>
      <uuid>acc-1</uuid>
      <name>Cash</name>
      <currencyCode>EUR</currencyCode>
      <transactions>
        <!-- First BUY account-transaction triggers inline portfolio serialisation -->
        <account-transaction>
          <uuid>acct-tx-1</uuid>
          <date>2021-04-13T16:25</date>
          <currencyCode>EUR</currencyCode>
          <amount>244683</amount>
          <security reference="../../../../../securities/security"/>
          <shares>0</shares>
          <crossEntry class="buysell">
            <!-- Portfolio serialised INLINE here (first occurrence) -->
            <portfolio>
              <uuid>port-1</uuid>
              <name>Comdirect</name>
              <isRetired>false</isRetired>
              <referenceAccount reference="../../../../.."/>
              <transactions>
                <!-- portfolio-transaction also serialised INLINE here -->
                <portfolio-transaction>
                  <uuid>port-tx-1</uuid>
                  <date reference="../../../../../date"/>
                  <currencyCode>EUR</currencyCode>
                  <amount>244683</amount>
                  <security reference="../../../../../../../../../securities/security"/>
                  <shares>5000000</shares>
                  <units>
                    <unit type="FEE">
                      <amount currency="EUR" amount="500"/>
                    </unit>
                  </units>
                  <type>BUY</type>
                </portfolio-transaction>
              </transactions>
            </portfolio>
            <portfolioTransaction reference="../portfolio/transactions/portfolio-transaction"/>
            <account reference="../../../../.."/>
            <accountTransaction reference="../.."/>
          </crossEntry>
          <type>BUY</type>
        </account-transaction>
        <!-- DEPOSIT has no crossEntry — always at top level -->
        <account-transaction>
          <uuid>acct-tx-2</uuid>
          <date>2021-01-01T00:00</date>
          <currencyCode>EUR</currencyCode>
          <amount>1000000000</amount>
          <shares>0</shares>
          <type>DEPOSIT</type>
        </account-transaction>
      </transactions>
    </account>
  </accounts>
  <portfolios>
    <!-- Portfolio already serialised above; only a back-reference here -->
    <portfolio
      reference="../accounts/account/transactions/account-transaction/crossEntry/portfolio"/>
  </portfolios>
</client>"""

    result = PortfolioPerformanceImporter().parse_bytes(xml.encode())

    types = {t.type for t in result.transactions}
    assert "BUY" in types, "BUY portfolio-transaction nested in crossEntry must be found"
    assert "DEPOSIT" in types

    buy_portfolio = next(
        t for t in result.transactions if t.type == "BUY" and t.kind == "portfolio"
    )
    assert buy_portfolio.shares == Decimal("5.000000")
    assert buy_portfolio.fees == Decimal("0.000500")
    assert buy_portfolio.security is not None
    assert buy_portfolio.security.ticker == "CBK.DE"

    # date was a reference — must be resolved to the account-transaction's date
    from datetime import datetime
    assert buy_portfolio.date == datetime(2021, 4, 13, 16, 25)

    buy_account = next(t for t in result.transactions if t.type == "BUY" and t.kind == "account")
    assert buy_account.uuid == "acct-tx-1"

    assert result.total_count == 3  # port-tx-1 (BUY), acct-tx-1 (BUY), acct-tx-2 (DEPOSIT)


@pytest.mark.asyncio
async def test_import_xml_post_shows_preview(client: AsyncClient) -> None:
    response = await client.post(
        "/import/xml",
        files={"file": ("portfolio.xml", SAMPLE_XML.encode("utf-8"), "application/xml")},
    )
    assert response.status_code == 200
    assert "File summary" in response.text
    assert "CBK.DE" in response.text
    assert "SAP.DE" in response.text
    assert "DIVIDENDS" in response.text
    assert "BUY" in response.text
