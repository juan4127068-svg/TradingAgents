"""Phase 1 gate tests — data validation and swing indicator calculations."""
import pytest
import pandas as pd
import numpy as np
from tradingagents.dataflows.exceptions import DataValidationError
from tradingagents.dataflows.indicator_utils import (
    calculate_atr,
    calculate_rsi,
    calculate_adx,
    detect_ema_crossover,
    build_swing_indicator_bundle,
)


def make_ohlcv(n=50, seed=42):
    """Generate synthetic OHLCV data for testing."""
    np.random.seed(seed)
    closes = 100 + np.cumsum(np.random.randn(n) * 0.5)
    return pd.DataFrame({
        "Open":   closes * 0.999,
        "High":   closes * 1.005,
        "Low":    closes * 0.995,
        "Close":  closes,
        "Volume": np.random.randint(500_000, 2_000_000, n).astype(float),
    })


# ── ATR ──────────────────────────────────────────────────────────────────────

class TestATR:
    def test_returns_positive_float(self):
        atr = calculate_atr(make_ohlcv(50), period=14)
        assert isinstance(atr, float)
        assert atr > 0

    def test_raises_on_insufficient_data(self):
        with pytest.raises(DataValidationError):
            calculate_atr(make_ohlcv(5), period=14)

    def test_raises_on_empty_dataframe(self):
        with pytest.raises(DataValidationError):
            calculate_atr(pd.DataFrame(), period=14)

    def test_result_is_rounded(self):
        atr = calculate_atr(make_ohlcv(50), period=14)
        assert atr == round(atr, 4)


# ── RSI ──────────────────────────────────────────────────────────────────────

class TestRSI:
    def test_values_in_range(self):
        rsi = calculate_rsi(make_ohlcv(50)["Close"], period=7)
        assert (rsi.dropna() >= 0).all()
        assert (rsi.dropna() <= 100).all()

    def test_returns_series(self):
        rsi = calculate_rsi(make_ohlcv(50)["Close"], period=7)
        assert isinstance(rsi, pd.Series)


# ── ADX ──────────────────────────────────────────────────────────────────────

class TestADX:
    def test_returns_float(self):
        adx = calculate_adx(make_ohlcv(60), period=14)
        assert isinstance(adx, float)
        assert adx >= 0

    def test_raises_on_insufficient_data(self):
        with pytest.raises(DataValidationError):
            calculate_adx(make_ohlcv(10), period=14)


# ── EMA crossover ─────────────────────────────────────────────────────────────

class TestEMACrossover:
    def test_returns_required_keys(self):
        result = detect_ema_crossover(make_ohlcv(100))
        for key in ("ema8_current", "ema21_current", "state", "fresh_cross", "signal"):
            assert key in result

    def test_state_is_bullish_or_bearish(self):
        result = detect_ema_crossover(make_ohlcv(100))
        assert result["state"] in ("bullish", "bearish")

    def test_raises_on_insufficient_data(self):
        with pytest.raises(DataValidationError):
            detect_ema_crossover(make_ohlcv(10))


# ── Full indicator bundle ─────────────────────────────────────────────────────

class TestSwingIndicatorBundle:
    def test_all_indicators_populated(self):
        daily = make_ohlcv(50)
        hourly = make_ohlcv(100)
        bundle = build_swing_indicator_bundle(
            ticker="TEST",
            df_daily=daily,
            df_1hr=hourly,
            vwap=105.50,
            rvol=1.8,
        )
        assert bundle["validation_passed"] is True
        for k, v in bundle["indicators"].items():
            assert v is not None, f"Indicator '{k}' is None"

    def test_no_nan_values(self):
        bundle = build_swing_indicator_bundle(
            ticker="TEST",
            df_daily=make_ohlcv(50),
            df_1hr=make_ohlcv(100),
            vwap=105.50,
            rvol=1.8,
        )
        for k, v in bundle["indicators"].items():
            if isinstance(v, float):
                assert not pd.isna(v), f"Indicator '{k}' is NaN"

    def test_daily_trend_is_valid(self):
        bundle = build_swing_indicator_bundle(
            ticker="TEST",
            df_daily=make_ohlcv(50),
            df_1hr=make_ohlcv(100),
            vwap=105.50,
            rvol=1.8,
        )
        assert bundle["indicators"]["daily_trend"] in ("BULLISH", "BEARISH")

    def test_empty_daily_raises(self):
        with pytest.raises(DataValidationError):
            build_swing_indicator_bundle(
                ticker="TEST",
                df_daily=pd.DataFrame(),
                df_1hr=make_ohlcv(100),
                vwap=100.0,
                rvol=1.0,
            )

    def test_empty_1hr_raises(self):
        with pytest.raises(DataValidationError):
            build_swing_indicator_bundle(
                ticker="TEST",
                df_daily=make_ohlcv(50),
                df_1hr=pd.DataFrame(),
                vwap=100.0,
                rvol=1.0,
            )
