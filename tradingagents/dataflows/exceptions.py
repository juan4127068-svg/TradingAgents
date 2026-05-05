class DataValidationError(Exception):
    """Raised when fetched market data fails validation checks."""

    def __init__(self, ticker: str, field: str, message: str):
        self.ticker = ticker
        self.field = field
        super().__init__(f"[{ticker}] Data validation failed for '{field}': {message}")


class InsufficientDataError(Exception):
    """Raised when there is not enough historical data for indicator calculation."""
    pass
