"""
Swing trade indicator calculations.
All functions raise DataValidationError rather than returning None or NaN.
"""
import pandas as pd
import numpy as np
from .exceptions import DataValidationError


def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Calculate Average True Range. Returns the most recent ATR value."""
    if df.empty or len(df) < period + 1:
        raise DataValidationError("unknown", "ATR",
            f"Need {period + 1} rows, got {len(df)}")

    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(span=period, adjust=False).mean()
    val = float(atr.iloc[-1])

    if pd.isna(val) or val <= 0:
        raise DataValidationError("unknown", "ATR", f"ATR calculation returned {val}")

    return round(val, 4)


def calculate_rsi(series: pd.Series, period: int = 7) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calculate_adx(df: pd.DataFrame, period: int = 14) -> float:
    """Returns the most recent ADX value. >25 indicates a trending market."""
    if df.empty or len(df) < period * 2:
        raise DataValidationError("unknown", "ADX",
            f"Need {period * 2} rows, got {len(df)}")

    high, low, close = df["High"], df["Low"], df["Close"]
    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)

    prev_close = close.shift(1)
    true_range = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr_series = true_range.ewm(span=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr_series)
    minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr_series)
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx = dx.ewm(span=period, adjust=False).mean()

    val = float(adx.iloc[-1])
    if pd.isna(val):
        raise DataValidationError("unknown", "ADX", "ADX returned NaN")
    return round(val, 2)


def detect_ema_crossover(df_1hr: pd.DataFrame) -> dict:
    """Detect EMA 8/21 crossover state on 1hr chart."""
    if len(df_1hr) < 22:
        raise DataValidationError("unknown", "EMA crossover",
            f"Need at least 22 rows, got {len(df_1hr)}")

    close = df_1hr["Close"]
    ema8 = calculate_ema(close, 8)
    ema21 = calculate_ema(close, 21)

    current_state = "bullish" if ema8.iloc[-1] > ema21.iloc[-1] else "bearish"
    prev_state = "bullish" if ema8.iloc[-2] > ema21.iloc[-2] else "bearish"
    fresh_cross = current_state != prev_state

    direction = "BULLISH" if current_state == "bullish" else "BEARISH"
    signal = f"{direction} crossover" if fresh_cross else f"{direction} trend continuing"

    return {
        "ema8_current": round(float(ema8.iloc[-1]), 4),
        "ema21_current": round(float(ema21.iloc[-1]), 4),
        "state": current_state,
        "fresh_cross": fresh_cross,
        "signal": signal,
    }


def build_swing_indicator_bundle(
    ticker: str,
    df_daily: pd.DataFrame,
    df_1hr: pd.DataFrame,
    vwap: float,
    rvol: float,
) -> dict:
    """
    Build the complete indicator bundle passed to the Market Analyst agent.
    Raises DataValidationError if any critical indicator cannot be calculated.
    """
    if df_daily.empty:
        raise DataValidationError(ticker, "OHLCV_daily", "Daily DataFrame is empty")
    if df_1hr.empty:
        raise DataValidationError(ticker, "OHLCV_1hr", "1hr DataFrame is empty")

    close_daily = df_daily["Close"]
    close_1hr = df_1hr["Close"]

    atr = calculate_atr(df_daily, period=14)
    rsi_7 = round(float(calculate_rsi(close_1hr, period=7).iloc[-1]), 2)
    adx = calculate_adx(df_daily, period=14)
    ema_cross = detect_ema_crossover(df_1hr)
    ema20_daily = calculate_ema(close_daily, 20)

    for name, val in [("RSI-7", rsi_7), ("ADX", adx), ("VWAP", vwap), ("RVOL", rvol)]:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            raise DataValidationError(ticker, name, f"{name} returned null")

    return {
        "ticker": ticker,
        "indicators": {
            "atr_14": atr,
            "rsi_7": rsi_7,
            "adx_14": adx,
            "vwap_session": round(float(vwap), 4),
            "relative_volume": rvol,
            "ema_8_1hr": ema_cross["ema8_current"],
            "ema_21_1hr": ema_cross["ema21_current"],
            "ema_crossover": ema_cross["signal"],
            "ema_20_daily": round(float(ema20_daily.iloc[-1]), 4),
            "daily_trend": "BULLISH" if close_daily.iloc[-1] > ema20_daily.iloc[-1] else "BEARISH",
            "current_price": round(float(close_daily.iloc[-1]), 4),
        },
        "validation_passed": True,
    }
