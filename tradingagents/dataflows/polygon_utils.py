"""
Polygon.io data fetcher for swing trading multi-timeframe candles.
Provides 15m, 1hr, and daily OHLCV data with VWAP and relative volume.
Requires POLYGON_API_KEY environment variable.
"""
import os
import pandas as pd
from datetime import datetime, timedelta
from .exceptions import DataValidationError

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")

_TF_MAP = {
    "15m": (15, "minute"),
    "1hr": (1, "hour"),
    "1d": (1, "day"),
}


def _get_client():
    if not POLYGON_API_KEY:
        raise DataValidationError("N/A", "POLYGON_API_KEY",
            "POLYGON_API_KEY is not set — add it to .env")
    from polygon import RESTClient
    return RESTClient(api_key=POLYGON_API_KEY)


def get_candles(
    ticker: str,
    timeframe: str,
    from_date: str,
    to_date: str,
    adjusted: bool = True,
) -> pd.DataFrame:
    """
    Fetch OHLCV candles from Polygon.io.

    Args:
        ticker:     Ticker symbol (e.g. "AAPL")
        timeframe:  "15m", "1hr", or "1d"
        from_date:  Start date "YYYY-MM-DD"
        to_date:    End date   "YYYY-MM-DD"
        adjusted:   Whether to use split/dividend-adjusted prices

    Returns:
        DataFrame indexed by datetime with columns Open/High/Low/Close/Volume/VWAP
    """
    if timeframe not in _TF_MAP:
        raise ValueError(f"Invalid timeframe '{timeframe}'. Use: {list(_TF_MAP)}")

    multiplier, timespan = _TF_MAP[timeframe]
    client = _get_client()

    bars = client.get_aggs(
        ticker=ticker,
        multiplier=multiplier,
        timespan=timespan,
        from_=from_date,
        to=to_date,
        adjusted=adjusted,
        sort="asc",
        limit=50000,
    )

    if not bars:
        raise DataValidationError(ticker, f"candles_{timeframe}",
            f"Polygon returned 0 bars for {from_date} to {to_date}")

    df = pd.DataFrame([{
        "datetime": pd.Timestamp(b.timestamp, unit="ms"),
        "Open":   b.open,
        "High":   b.high,
        "Low":    b.low,
        "Close":  b.close,
        "Volume": b.volume,
        "VWAP":   getattr(b, "vwap", None),
    } for b in bars])

    df.set_index("datetime", inplace=True)
    return df


def get_relative_volume(ticker: str, date: str, lookback_days: int = 20) -> float:
    """
    Calculate relative volume: today's volume vs N-day average.
    Returns ratio (e.g. 1.8 means 80% above the average).
    """
    to_dt = datetime.strptime(date, "%Y-%m-%d")
    from_dt = to_dt - timedelta(days=lookback_days + 7)  # buffer for weekends/holidays

    df = get_candles(ticker, "1d", from_dt.strftime("%Y-%m-%d"), date)

    if len(df) < 5:
        raise DataValidationError(ticker, "relative_volume",
            f"Insufficient history: only {len(df)} days")

    avg_volume = df["Volume"].iloc[:-1].tail(lookback_days).mean()
    today_volume = df["Volume"].iloc[-1]

    if avg_volume == 0:
        raise DataValidationError(ticker, "relative_volume", "Average volume is zero")

    return round(float(today_volume / avg_volume), 2)


def get_session_vwap(ticker: str, date: str) -> float:
    """
    Get the session VWAP for a given date from 1hr bars.
    Falls back to a manual typical-price calculation if the field is missing.
    """
    df = get_candles(ticker, "1hr", date, date)

    if df.empty:
        raise DataValidationError(ticker, "vwap", f"No 1hr bars returned for {date}")

    if "VWAP" in df.columns and df["VWAP"].notna().all():
        return round(float(df["VWAP"].iloc[-1]), 4)

    # Manual fallback: VWAP = cumsum(typical_price × volume) / cumsum(volume)
    df["TP"] = (df["High"] + df["Low"] + df["Close"]) / 3
    df["TPV"] = df["TP"] * df["Volume"]
    vwap = df["TPV"].cumsum().iloc[-1] / df["Volume"].cumsum().iloc[-1]
    return round(float(vwap), 4)
