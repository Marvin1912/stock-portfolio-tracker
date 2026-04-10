"""PDF parser foundation for broker statement imports."""

from __future__ import annotations

import abc
from decimal import Decimal
from pathlib import Path


class BaseBrokerParser(abc.ABC):
    """Abstract base for broker-specific PDF parsers.

    Subclasses implement :meth:`extract` for a particular broker's layout.
    Each parser returns only the data needed to upsert a holding: the ticker
    symbol and the share quantity.
    """

    @abc.abstractmethod
    def extract(self, pdf_path: Path) -> list[tuple[str, Decimal]]:
        """Extract holdings from a broker PDF.

        Parameters
        ----------
        pdf_path:
            Path to the PDF file produced by the broker.

        Returns
        -------
        list[tuple[str, Decimal]]
            A list of ``(ticker, quantity)`` pairs, one per holding found in
            the document.  Ticker symbols are returned upper-cased and
            stripped of whitespace.
        """
