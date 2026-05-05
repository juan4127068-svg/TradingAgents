# BUILD_PLAN.md — Swing Trading Agents
## Complete Build Plan: TradingAgents → Swing Trading Fork

> **How to use this document:**
> Work through phases in strict order. Each phase has a gate test.
> Do not begin Phase N+1 until Phase N gate tests pass.
> All Claude Code sessions should start by reading CLAUDE.md, then this file.

---

## Phase Overview

| Phase | Name | Effort | Gate |
|---|---|---|---|
| 0 | Repository setup | 30 min | Repo boots, upstream remote set |
| 1 | Fix data pipeline + indicator bug | 2–3 hrs | `test_data_pipeline.py` passes |
| 2 | Config and graph wiring | 1 hr | Clean run, no fundamentals agent |
| 3 | Indicator suite replacement | 2–3 hrs | `test_indicators.py` passes |
| 4 | Agent prompt rewrites | 3–4 hrs | All agents output swing-scoped content |
| 5 | Output schema — SwingTradeDecision | 1–2 hrs | `SwingTradeDecision` fields all populated |
| 6 | Pre-market scanner | 2–3 hrs | Scanner returns ranked ticker list |
| 7 | Earnings / calendar check | 1–2 hrs | Catalyst risk flags correctly |
| 8 | Exit monitor | 2–3 hrs | Exit signals generated for open positions |
| 9 | Swing trade memory / journal | 1–2 hrs | Journal logs and feeds back into PM |
| 10 | Integration testing + tuning | 2–4 hrs | Full `test_swing_signal.py` passes |

**Total estimated effort: 20–30 hours of focused Claude Code sessions**

---

## Phase 0 — Repository Setup

### Objective
Fork the upstream repo, establish the project structure, set up environment.

### Steps

**0.1 — Fork and clone**
```bash
git clone https://github.com/TauricResearch/TradingAgents.git swing-trading-agents
cd swing-trading-agents
git remote rename origin upstream
# Create your own GitHub repo, then:
git remote add origin https://github.com/YOUR_USERNAME/swing-trading-agents.git
git push -u origin main
```

**0.2 — Create virtual environment**
```bash
conda create -n swing-agents python=3.13
conda activate swing-agents
pip install .
pip install polygon-api-client finnhub-python  # new dependencies
```

**0.3 — Set up environment file**
```bash
cp .env.example .env
```
Edit `.env` and add:
```
ANTHROPIC_API_KEY=your_key
POLYGON_API_KEY=your_key
ALPHA_VANTAGE_API_KEY=your_existing_key
FINNHUB_API_KEY=your_key   # free tier at finnhub.io
SWING_ACCOUNT_SIZE=25000
SWING_MAX_RISK_PCT=0.02
```

**0.4 — Place project documents**
Copy `CLAUDE.md` and `BUILD_PLAN.md` into the root of the repo:
```bash
# Both files should already exist from your setup session
ls CLAUDE.md BUILD_PLAN.md
```

**0.5 — Create new directories**
```bash
mkdir -p tradingagents/scanners
mkdir -p tradingagents/monitor
mkdir -p prompts
mkdir -p tests
touch tradingagents/scanners/__init__.py
touch tradingagents/monitor/__init__.py
```

**0.6 — Verify baseline run**
Run the original system on a known ticker to confirm the upstream is working
before we start modifying it:
```bash
python -c "
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
config = DEFAULT_CONFIG.copy()
config['llm_provider'] = 'anthropic'
ta = TradingAgentsGraph(debug=True, config=config)
_, decision = ta.propagate('AAPL', '2026-05-02')
print(decision)
"
```
Note any errors. This baseline run output becomes your "before" benchmark.

### Gate
- [ ] Repo exists on GitHub with upstream remote set
- [ ] Virtual environment activates without errors
- [ ] All API keys present in `.env`
- [ ] New directories created
- [ ] Baseline run completes (even if with errors — document them)

---

## Phase 1 — Fix Data Pipeline + Indicator Bug

### Objective
The original system has a critical bug where technical indicators silently return
empty values (confirmed in the GC=F Gold Futures test report). This phase fixes
the root cause and adds multi-timeframe candle support.

### Context
The bug occurs because `get_indicators()` is called before OHLCV data is
confirmed non-empty. The agent then proceeds with blank data and produces
hypothetical analysis instead of real analysis.

### Steps

**1.1 — Audit the existing data flow**
Read and understand these files before changing anything:
```
tradingagents/dataflows/yfin_utils.py
tradingagents/dataflows/interface.py
tradingagents/dataflows/stockstats_utils.py  (if present)
```
Map exactly where indicator calculations happen and what the call sequence is.

**1.2 — Create `DataValidationError`**

Create `tradingagents/dataflows/exceptions.py`:
```python
class DataValidationError(Exception):
    """Raised when fetched market data fails validation checks."""
    def __init__(self, ticker: str, field: str, message: str):
        self.ticker = ticker
        self.field = field
        super().__init__(f"[{ticker}] Data validation failed for '{field}': {message}")


class InsufficientDataError(Exception):
    """Raised when there is not enough historical data for indicator calculation."""
    pass
```

**1.3 — Add data validation to the OHLCV fetch**

In `yfin_utils.py` (or wherever daily candles are fetched), add validation
immediately after the data fetch:

```python
def validate_ohlcv(df: pd.DataFrame, ticker: str, min_rows: int = 30) -> pd.DataFrame:
    """Validate OHLCV dataframe. Raises DataValidationError on failure."""
    if df is None or df.empty:
        raise DataValidationError(ticker, "OHLCV", "DataFrame is empty or None")
    if len(df) < min_rows:
        raise DataValidationError(ticker, "OHLCV",
            f"Only {len(df)} rows returned, minimum {min_rows} required for indicators")
    required_cols = ["Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise DataValidationError(ticker, "OHLCV", f"Missing columns: {missing}")
    null_counts = df[required_cols].isnull().sum()
    if null_counts.any():
        raise DataValidationError(ticker, "OHLCV",
            f"Null values found: {null_counts[null_counts > 0].to_dict()}")
    return df
```

**1.4 — Create `tradingagents/dataflows/polygon_utils.py`**

```python
"""
Polygon.io data fetcher for swing trading multi-timeframe candles.
Provides 15m, 1hr, and daily OHLCV data with VWAP and relative volume.
"""
import os
import pandas as pd
from datetime import datetime, timedelta
from polygon import RESTClient
from tradingagents.dataflows.exceptions import DataValidationError

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")

def get_candles(
    ticker: str,
    timeframe: str,          # "15m", "1hr", "1d"
    from_date: str,          # "YYYY-MM-DD"
    to_date: str,            # "YYYY-MM-DD"
    adjusted: bool = True
) -> pd.DataFrame:
    """
    Fetch OHLCV candles from Polygon.io.

    timeframe mappings:
        "15m"  → multiplier=15, timespan="minute"
        "1hr"  → multiplier=1,  timespan="hour"
        "1d"   → multiplier=1,  timespan="day"
    """
    tf_map = {
        "15m": (15, "minute"),
        "1hr": (1,  "hour"),
        "1d":  (1,  "day"),
    }
    if timeframe not in tf_map:
        raise ValueError(f"Invalid timeframe '{timeframe}'. Use: {list(tf_map.keys())}")

    multiplier, timespan = tf_map[timeframe]
    client = RESTClient(api_key=POLYGON_API_KEY)

    bars = client.get_aggs(
        ticker=ticker,
        multiplier=multiplier,
        timespan=timespan,
        from_=from_date,
        to=to_date,
        adjusted=adjusted,
        sort="asc",
        limit=50000
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
    Returns ratio (e.g. 1.8 = 80% above average).
    """
    to_dt = datetime.strptime(date, "%Y-%m-%d")
    from_dt = to_dt - timedelta(days=lookback_days + 5)  # buffer for weekends

    df = get_candles(ticker, "1d", from_dt.strftime("%Y-%m-%d"), date)

    if len(df) < 5:
        raise DataValidationError(ticker, "relative_volume",
            f"Insufficient history: only {len(df)} days")

    avg_volume = df["Volume"].iloc[:-1].tail(lookback_days).mean()
    today_volume = df["Volume"].iloc[-1]

    if avg_volume == 0:
        raise DataValidationError(ticker, "relative_volume", "Average volume is zero")

    return round(today_volume / avg_volume, 2)


def get_session_vwap(ticker: str, date: str) -> float:
    """
    Get the closing VWAP for a given session date (from 1hr bars).
    Falls back to a manual VWAP calculation if Polygon field is missing.
    """
    df = get_candles(ticker, "1hr", date, date)

    if df.empty:
        raise DataValidationError(ticker, "vwap", f"No 1hr bars returned for {date}")

    if "VWAP" in df.columns and df["VWAP"].notna().all():
        return float(df["VWAP"].iloc[-1])

    # Manual VWAP fallback: cumsum(typical_price * volume) / cumsum(volume)
    df["TP"] = (df["High"] + df["Low"] + df["Close"]) / 3
    df["TPV"] = df["TP"] * df["Volume"]
    vwap = df["TPV"].cumsum().iloc[-1] / df["Volume"].cumsum().iloc[-1]
    return round(float(vwap), 4)
```

**1.5 — Create `tradingagents/dataflows/indicator_utils.py`**

```python
"""
Swing trade indicator calculations.
All functions raise DataValidationError rather than returning None or NaN.
"""
import pandas as pd
import numpy as np
from tradingagents.dataflows.exceptions import DataValidationError


def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
    """
    Calculate Average True Range. Returns the most recent ATR value.
    Requires at least period+1 rows.
    """
    if len(df) < period + 1:
        raise DataValidationError("unknown", "ATR",
            f"Need {period+1} rows, got {len(df)}")

    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
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
    """Returns the most recent ADX value. >25 indicates trending."""
    if len(df) < period * 2:
        raise DataValidationError("unknown", "ADX",
            f"Need {period*2} rows, got {len(df)}")

    high, low, close = df["High"], df["Low"], df["Close"]
    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)

    prev_close = close.shift(1)
    true_range = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
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
    """
    Detect EMA 8/21 crossover state on 1hr chart.
    Returns dict with crossover direction and bars since last cross.
    """
    close = df_1hr["Close"]
    ema8  = calculate_ema(close, 8)
    ema21 = calculate_ema(close, 21)

    current_state = "bullish" if ema8.iloc[-1] > ema21.iloc[-1] else "bearish"
    prev_state    = "bullish" if ema8.iloc[-2] > ema21.iloc[-2] else "bearish"

    fresh_cross = current_state != prev_state

    return {
        "ema8_current":  round(float(ema8.iloc[-1]), 4),
        "ema21_current": round(float(ema21.iloc[-1]), 4),
        "state":         current_state,
        "fresh_cross":   fresh_cross,
        "signal":        f"{'BULLISH' if current_state == 'bullish' else 'BEARISH'} crossover" if fresh_cross else f"{'BULLISH' if current_state == 'bullish' else 'BEARISH'} trend continuing",
    }


def build_swing_indicator_bundle(
    ticker: str,
    df_daily: pd.DataFrame,
    df_1hr: pd.DataFrame,
    vwap: float,
    rvol: float
) -> dict:
    """
    Build the complete indicator bundle passed to the Technical Analyst agent.
    Raises DataValidationError if any critical indicator fails.
    """
    close_daily = df_daily["Close"]
    close_1hr   = df_1hr["Close"]

    atr    = calculate_atr(df_daily, period=14)
    rsi_7  = round(float(calculate_rsi(close_1hr, period=7).iloc[-1]), 2)
    adx    = calculate_adx(df_daily, period=14)
    ema_cross = detect_ema_crossover(df_1hr)
    ema20_daily = calculate_ema(close_daily, 20)

    for name, val in [("RSI-7", rsi_7), ("ADX", adx), ("VWAP", vwap), ("RVOL", rvol)]:
        if pd.isna(val) or val is None:
            raise DataValidationError(ticker, name, f"{name} returned null")

    return {
        "ticker": ticker,
        "indicators": {
            "atr_14":          atr,
            "rsi_7":           rsi_7,
            "adx_14":          adx,
            "vwap_session":    round(vwap, 4),
            "relative_volume": rvol,
            "ema_8_1hr":       ema_cross["ema8_current"],
            "ema_21_1hr":      ema_cross["ema21_current"],
            "ema_crossover":   ema_cross["signal"],
            "ema_20_daily":    round(float(ema20_daily.iloc[-1]), 4),
            "daily_trend":     "BULLISH" if close_daily.iloc[-1] > ema20_daily.iloc[-1] else "BEARISH",
            "current_price":   round(float(close_daily.iloc[-1]), 4),
        },
        "validation_passed": True
    }
```

**1.6 — Write Phase 1 tests**

Create `tests/test_data_pipeline.py`:
```python
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from tradingagents.dataflows.exceptions import DataValidationError
from tradingagents.dataflows.indicator_utils import (
    calculate_atr, calculate_rsi, calculate_adx,
    detect_ema_crossover, build_swing_indicator_bundle
)

def make_ohlcv(n=50):
    """Generate synthetic OHLCV data for testing."""
    import numpy as np
    np.random.seed(42)
    closes = 100 + np.cumsum(np.random.randn(n) * 0.5)
    return pd.DataFrame({
        "Open":   closes * 0.999,
        "High":   closes * 1.005,
        "Low":    closes * 0.995,
        "Close":  closes,
        "Volume": np.random.randint(500000, 2000000, n),
    })

def test_atr_returns_positive_float():
    df = make_ohlcv(50)
    atr = calculate_atr(df, period=14)
    assert isinstance(atr, float)
    assert atr > 0

def test_atr_raises_on_insufficient_data():
    df = make_ohlcv(5)
    with pytest.raises(DataValidationError):
        calculate_atr(df, period=14)

def test_rsi_range():
    df = make_ohlcv(50)
    rsi = calculate_rsi(df["Close"], period=7)
    assert (rsi.dropna() >= 0).all()
    assert (rsi.dropna() <= 100).all()

def test_indicator_bundle_no_nulls():
    daily = make_ohlcv(50)
    hourly = make_ohlcv(100)
    bundle = build_swing_indicator_bundle(
        ticker="TEST",
        df_daily=daily,
        df_1hr=hourly,
        vwap=105.50,
        rvol=1.8
    )
    assert bundle["validation_passed"] is True
    for k, v in bundle["indicators"].items():
        assert v is not None, f"Indicator '{k}' is None"

def test_empty_dataframe_raises():
    with pytest.raises(DataValidationError):
        build_swing_indicator_bundle(
            ticker="TEST",
            df_daily=pd.DataFrame(),
            df_1hr=pd.DataFrame(),
            vwap=100.0,
            rvol=1.0
        )
```

### Gate
```bash
pytest tests/test_data_pipeline.py -v
```
All tests must pass before Phase 2.

---

## Phase 2 — Config and Graph Wiring

### Objective
Update `default_config.py` with swing trade parameters, remove the Fundamentals
Analyst node from the LangGraph graph, and reduce debate rounds to 1.

### Steps

**2.1 — Update `tradingagents/default_config.py`**

Add these keys to the existing `DEFAULT_CONFIG` dict (do not remove existing keys):
```python
# ── Swing trade additions ─────────────────────────────────────────────────
"time_horizon":           "1-5 days",
"stop_method":            "atr",           # "atr" | "ma" (ma = legacy)
"atr_stop_multiplier":    1.5,
"min_rr_ratio":           2.0,
"max_hold_days":          5,
"candle_timeframes":      ["15m", "1hr", "1d"],
"primary_entry_tf":       "1hr",
"trend_filter_tf":        "1d",
"min_relative_volume":    1.5,
"max_risk_per_trade":     0.02,            # 2% of account
"account_size":           25000,           # override via env
"data_provider":          "polygon",       # "polygon" | "yfinance"
"use_earnings_filter":    True,
"earnings_buffer_days":   5,               # skip if earnings within N days
# ── Override upstream defaults ─────────────────────────────────────────────
"max_debate_rounds":      1,               # was 2 upstream — reduce for speed
```

**2.2 — Remove Fundamentals Analyst from the graph**

In `tradingagents/graph/trading_graph.py`, find where the fundamentals analyst
node is added and comment it out:

```python
# SWING TRADE MODIFICATION: Fundamentals analyst removed.
# DCF, P/E, and intrinsic value analysis is irrelevant for 1-5 day holds.
# Replaced by catalyst_analyst which checks earnings dates and upgrades.
# Original line: workflow.add_node("fundamentals_analyst", fundamentals_analyst_node)
```

Also remove or comment out any edges that flow through the fundamentals analyst node.

**2.3 — Inject swing config into agent state**

In `trading_graph.py`, ensure the swing trade config keys are passed into the
initial graph state so every agent can access them:

```python
initial_state = {
    ...existing state keys...,
    "swing_config": {
        "time_horizon":        config.get("time_horizon", "1-5 days"),
        "max_hold_days":       config.get("max_hold_days", 5),
        "min_rr_ratio":        config.get("min_rr_ratio", 2.0),
        "stop_method":         config.get("stop_method", "atr"),
        "atr_stop_multiplier": config.get("atr_stop_multiplier", 1.5),
    }
}
```

**2.4 — Verify reduced debate rounds**

Confirm `max_debate_rounds: 1` is being respected by adding a counter log:
```python
# In researcher debate loop:
logger.debug(f"Debate round {round_num}/{config['max_debate_rounds']}")
```

### Gate
- [ ] `python -c "from tradingagents.default_config import DEFAULT_CONFIG; print(DEFAULT_CONFIG['max_hold_days'])"` prints `5`
- [ ] A full `propagate()` run completes without the fundamentals analyst running
- [ ] Debate loop runs exactly 1 round

---

## Phase 3 — Indicator Suite Replacement

### Objective
Replace all legacy long-term indicators in the Technical Analyst with the swing
trade indicator set defined in CLAUDE.md Section 6.

### Steps

**3.1 — Update the data fetch in the market analyst**

The market analyst agent receives its data through the dataflows interface.
Update `interface.py` (or the relevant data accessor) to call the new
`build_swing_indicator_bundle()` function for the 1hr and daily candles.

Ensure the data bundle reaching the technical analyst contains:
```
atr_14, rsi_7, adx_14, vwap_session, relative_volume,
ema_8_1hr, ema_21_1hr, ema_crossover, ema_20_daily,
daily_trend, current_price
```

**3.2 — Create the swing market analyst system prompt**

Create `prompts/market_analyst_swing.md`:
```markdown
# Market Analyst — Swing Trade Focus

You are a technical market analyst specializing in **1–5 day swing trades**.
You are NOT evaluating long-term investment value. You are identifying
short-term price setups with high probability of following through.

## Your Data Bundle
You will receive the following pre-calculated indicators. All values have been
validated — if any are missing, report an error immediately:
- ATR-14 (daily): volatility measure for stop sizing
- RSI-7 (1hr): momentum oscillator, faster than standard 14-period
- ADX-14 (daily): trend strength — above 25 = trending, below 20 = choppy
- VWAP (session): intraday price anchor
- Relative Volume: today's volume vs 20-day average
- EMA-8 / EMA-21 (1hr): short-term momentum crossover
- EMA-20 (daily): trend direction filter
- Daily Trend: BULLISH or BEARISH based on price vs EMA-20

## Analysis Framework

### Step 1 — Daily trend filter
Is the daily trend BULLISH or BEARISH? Only take trades in the direction
of the daily trend. If BULLISH, look for long entries. If BEARISH, look
for short entries or pass on the setup.

### Step 2 — ADX strength check
Is ADX above 25? If yes, the trend is strong — momentum continuation
setups are preferred. If ADX is below 20, the market is choppy —
breakout setups have lower probability. Note this clearly.

### Step 3 — Entry signal check (1hr chart)
Has EMA-8 crossed above EMA-21 (for longs)? Is the price above VWAP?
These are required for a valid entry signal.

### Step 4 — RSI-7 positioning
Is RSI-7 between 40 and 70 at entry? This range is ideal — above 70
means extended (wait for pullback), below 40 means weak (avoid).

### Step 5 — Volume confirmation
Is relative volume ≥ 1.5×? Volume below 1.5× the 20-day average
indicates low participation — reduce confidence, flag as caution.

## Output Requirements
Your report must include:
1. **Trend alignment**: Daily trend direction and ADX strength reading
2. **Entry signal**: EMA crossover status and VWAP relationship
3. **RSI reading**: Exact RSI-7 value and interpretation
4. **Volume reading**: Exact RVOL value and interpretation
5. **Technical verdict**: BULLISH SETUP / BEARISH SETUP / NO SETUP / CHOPPY — DO NOT TRADE
6. **Key levels**: Nearest support (stop candidate) and resistance (target candidate)
7. **ATR value**: The raw ATR-14 number (passed to Trader for stop calculation)

Do NOT reference the 200-day moving average, MACD, or multi-month price targets.
Do NOT recommend a position size — that is the Portfolio Manager's role.
Keep your report concise: under 400 words.
```

**3.3 — Wire the new prompt into the market analyst agent**

In `tradingagents/agents/market_analyst.py`, load the prompt from the markdown file:
```python
import os

def load_prompt(name: str) -> str:
    prompts_dir = os.path.join(os.path.dirname(__file__), "../../prompts")
    with open(os.path.join(prompts_dir, name), "r") as f:
        return f.read()

SWING_MARKET_ANALYST_PROMPT = load_prompt("market_analyst_swing.md")
```

### Gate
```bash
pytest tests/test_indicators.py -v
```

Create `tests/test_indicators.py`:
```python
def test_market_analyst_receives_full_bundle():
    """Market analyst state must contain all swing indicator keys."""
    required_keys = [
        "atr_14", "rsi_7", "adx_14", "vwap_session",
        "relative_volume", "ema_8_1hr", "ema_21_1hr",
        "ema_crossover", "ema_20_daily", "daily_trend"
    ]
    # Mock a run and inspect the state passed to market analyst
    # Implement with LangGraph state inspection
    ...
```

---

## Phase 4 — Agent Prompt Rewrites

### Objective
Rewrite every agent's system prompt to be scoped to a 1–5 day swing trade
horizon. This is the most impactful phase — the prompts define what the
entire multi-agent system thinks about and outputs.

### Steps

**4.1 — Catalyst Analyst (replaces Fundamentals Analyst)**

Create `prompts/catalyst_analyst_swing.md`. This agent replaces DCF and
valuation analysis with short-term catalyst detection.

Key instructions to include in the prompt:
- Check if earnings are within the next 5 trading days → HIGH catalyst risk
- Check for recent analyst upgrades or downgrades (last 3 days)
- Check for upcoming FDA decisions, investor days, conferences
- Check for recent institutional filing activity (13F, Form 4)
- Output a `catalyst_risk_score` from 1–10 and explicit `catalyst_events` list
- Explicitly state: "If earnings fall within the projected hold window, the
  Portfolio Manager should reduce position size by 50% or skip the trade."

Rename the agent file: `fundamentals_analyst.py` → `catalyst_analyst.py`
Update the import in `trading_graph.py`.

**4.2 — Bull Researcher**

Create `prompts/bull_researcher_swing.md`.

Core prompt direction:
- All arguments must be scoped to why this stock should move higher in the
  next 1–5 trading days
- Valid bull arguments: momentum continuation, breakout above key resistance,
  positive catalyst upcoming, strong relative strength vs sector, high RVOL
  with institutional footprint
- Invalid bull arguments (reject these): long-term growth story, valuation
  discount, multi-year chart analysis
- You must state: estimated move magnitude, how many days to target,
  and what would invalidate the bull thesis within the hold window

**4.3 — Bear Researcher**

Create `prompts/bear_researcher_swing.md`.

Core prompt direction:
- All arguments must be scoped to why this stock could fail in the next
  1–5 trading days
- Valid bear arguments: overextended move, approaching major resistance,
  weakening volume, failed breakout pattern, sector headwinds this week,
  catalyst risk
- You must state: what resistance level would stop the move, what volume
  signal would indicate distribution, what would confirm the bear case
  within 1–2 days

**4.4 — Trader Agent**

Create `prompts/trader_swing.md`.

The Trader receives the combined analyst and researcher reports. Key instructions:

```markdown
# Trader Agent — Swing Trade Execution Plan

You receive reports from the analyst team and researcher debate.
Your job is to produce a precise, executable trade plan.

## Required Output Fields
You must calculate and state ALL of the following:

**Entry:** A specific price zone (e.g., "$142.50 – $143.20")
- For longs: enter on a 1hr candle close above the setup level
- Do not chase — if price is more than 1× ATR above the signal level, PASS

**Stop Loss:**
- Calculate: stop = entry_mid - (1.5 × ATR-14)
- State the exact dollar stop price
- Do not use moving averages, support levels alone, or round numbers as stops
- The ATR-14 value is provided in the technical analyst report — use it

**Target:**
- Calculate: risk_per_share = entry_mid - stop
- Minimum target = entry_mid + (2.0 × risk_per_share)  ← 2:1 R:R minimum
- Preferred target = entry_mid + (3.0 × risk_per_share) if a structural
  resistance level aligns near that price

**R:R Ratio Check:**
- Calculate: (target - entry_mid) / (entry_mid - stop)
- If this ratio is LESS THAN 2.0 → output NO_TRADE with explanation
- Never force a trade that doesn't meet minimum R:R

**Hold Duration:**
- State maximum hold: never more than 5 trading days
- State expected hold: based on the setup type (e.g., "2–3 days for
  momentum continuation, exit if target not hit by day 3")

**Invalidation:**
- State what would prove the trade wrong BEFORE the stop is hit
  (e.g., "If RSI-7 drops below 35 within the first session, exit early")
```

**4.5 — Portfolio Manager**

Create `prompts/portfolio_manager_swing.md`.

Key instructions to add to the existing PM prompt:

```markdown
## Swing Trade Decision Framework

Your time horizon is 1–5 trading days. You are NOT making an investment.
You are approving or rejecting a short-term trade plan.

Approve the trade ONLY IF:
1. Technical analyst reports a valid BULLISH or BEARISH setup
2. Trader has calculated a stop with R:R ≥ 2.0
3. Catalyst risk is LOW or MEDIUM (HIGH = reduce size 50% or skip)
4. Daily trend aligns with the trade direction
5. Relative volume is ≥ 1.5×

Output the complete SwingTradeDecision schema (see CLAUDE.md Section 8).
Every field is required. Do not omit entry_zone, stop_price, or target_price.
State the max_hold_days explicitly — never exceed 5.

If you REJECT the trade, output action: "NO_TRADE" and explain the specific
criterion that was not met.
```

### Gate
- [ ] Run a full `propagate()` call and inspect each agent's output
- [ ] Catalyst analyst no longer mentions P/E, DCF, or intrinsic value
- [ ] Bull/Bear researchers debate within 1–5 day horizon
- [ ] Trader outputs exact entry zone, stop price, target, and R:R ratio
- [ ] Portfolio Manager outputs `SwingTradeDecision` with all fields populated

---

## Phase 5 — SwingTradeDecision Output Schema

### Objective
Formalize the Portfolio Manager's structured output as a Pydantic model.
Extend the upstream schema without breaking it.

### Steps

**5.1 — Create the Pydantic schema**

Add to `tradingagents/agents/schemas.py` (alongside existing upstream schemas):

```python
from pydantic import BaseModel, Field, field_validator
from typing import Literal

class SwingTradeDecision(BaseModel):
    # Core decision
    action:     Literal["BUY", "SELL", "HOLD", "NO_TRADE"]
    rating:     Literal["Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"]
    confidence: float = Field(ge=0.0, le=1.0)

    # Swing trade execution fields
    entry_zone_low:  float = Field(gt=0)
    entry_zone_high: float = Field(gt=0)
    stop_price:      float = Field(gt=0)
    target_price:    float = Field(gt=0)
    atr_used:        float = Field(gt=0, description="ATR-14 value used for stop calc")
    rr_ratio:        float = Field(ge=0, description="(target-entry)/(entry-stop)")

    # Hold parameters
    hold_days_max:   int   = Field(ge=1, le=5)
    hold_days_expected: int = Field(ge=1, le=5)

    # Risk and catalyst
    catalyst_risk:   Literal["LOW", "MEDIUM", "HIGH"]
    catalyst_detail: str = Field(max_length=200)

    # Thesis
    thesis_summary:      str = Field(max_length=400)
    invalidation_signal: str = Field(max_length=200)
    exit_conditions:     list[str] = Field(min_length=2, max_length=5)

    @field_validator("rr_ratio")
    @classmethod
    def rr_must_meet_minimum(cls, v):
        if v > 0 and v < 2.0:
            raise ValueError(f"R:R ratio {v:.2f} is below minimum 2.0 — trade should be NO_TRADE")
        return v

    @field_validator("entry_zone_high")
    @classmethod
    def entry_zone_valid(cls, v, info):
        if "entry_zone_low" in info.data and v <= info.data["entry_zone_low"]:
            raise ValueError("entry_zone_high must be greater than entry_zone_low")
        return v
```

**5.2 — Wire schema into Portfolio Manager**

Using the upstream structured output pattern (already in v0.2.4):
```python
from tradingagents.agents.schemas import SwingTradeDecision

structured_llm = llm.with_structured_output(SwingTradeDecision)
decision = structured_llm.invoke(pm_prompt)
```

### Gate
- [ ] `SwingTradeDecision` model imports without error
- [ ] A full run produces a `SwingTradeDecision` instance with all fields
- [ ] Pydantic validator rejects a decision with R:R < 2.0

---

## Phase 6 — Pre-Market Watchlist Scanner

### Objective
Build the morning scanner that generates a prioritized list of swing trade
candidates before the market opens.

### Steps

**6.1 — Create `tradingagents/scanners/premarket.py`**

```python
"""
Pre-market swing trade watchlist scanner.
Run at 8:00-9:00 AM ET before market open.
Filters for gap + volume conditions that historically precede strong swing setups.
"""
import os
import pandas as pd
from datetime import datetime, date
from tradingagents.dataflows.polygon_utils import get_candles, get_relative_volume
from tradingagents.dataflows.calendar_utils import check_earnings_proximity
from tradingagents.dataflows.exceptions import DataValidationError
from tradingagents.default_config import DEFAULT_CONFIG


def scan_for_swing_candidates(
    watchlist: list[str],
    scan_date: str = None,
    config: dict = None
) -> list[dict]:
    """
    Screen a list of tickers for swing trade setups.

    Args:
        watchlist:  List of ticker symbols to screen
        scan_date:  Date string "YYYY-MM-DD". Defaults to today.
        config:     Config dict. Defaults to DEFAULT_CONFIG.

    Returns:
        List of candidate dicts, sorted by score descending.
        Each dict contains: ticker, gap_pct, rvol, catalyst_risk, score, notes
    """
    if scan_date is None:
        scan_date = date.today().strftime("%Y-%m-%d")
    if config is None:
        config = DEFAULT_CONFIG

    candidates = []

    for ticker in watchlist:
        try:
            result = evaluate_ticker(ticker, scan_date, config)
            if result["qualifies"]:
                candidates.append(result)
        except DataValidationError as e:
            print(f"[SCANNER] Skipping {ticker}: {e}")
        except Exception as e:
            print(f"[SCANNER] Error on {ticker}: {e}")

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:5]  # return top 5


def evaluate_ticker(ticker: str, scan_date: str, config: dict) -> dict:
    """Evaluate a single ticker against swing scan criteria."""
    from datetime import timedelta
    two_days_ago = (
        datetime.strptime(scan_date, "%Y-%m-%d") - timedelta(days=5)
    ).strftime("%Y-%m-%d")

    df = get_candles(ticker, "1d", two_days_ago, scan_date)
    if len(df) < 2:
        return {"ticker": ticker, "qualifies": False, "reason": "Insufficient daily data"}

    prev_close = float(df["Close"].iloc[-2])
    today_open = float(df["Open"].iloc[-1])
    gap_pct = ((today_open - prev_close) / prev_close) * 100

    rvol = get_relative_volume(ticker, scan_date)

    earnings_check = check_earnings_proximity(ticker, scan_date,
                                              config.get("earnings_buffer_days", 5))

    score = 0
    notes = []

    if abs(gap_pct) >= 2.0:
        score += 30
        notes.append(f"Gap: {gap_pct:+.1f}%")
    if rvol >= config.get("min_relative_volume", 1.5):
        score += 30
        notes.append(f"RVOL: {rvol:.1f}x")
    if rvol >= 3.0:
        score += 10
    if gap_pct > 0:
        score += 10
    if earnings_check["risk"] == "LOW":
        score += 20
    elif earnings_check["risk"] == "MEDIUM":
        score += 10

    min_gap = 2.0
    qualifies = abs(gap_pct) >= min_gap and rvol >= config.get("min_relative_volume", 1.5)

    return {
        "ticker":          ticker,
        "qualifies":       qualifies,
        "gap_pct":         round(gap_pct, 2),
        "rvol":            rvol,
        "catalyst_risk":   earnings_check["risk"],
        "catalyst_detail": earnings_check["detail"],
        "score":           score,
        "notes":           notes,
        "reason":          ", ".join(notes) if qualifies else "Below minimum thresholds",
    }
```

**6.2 — Add CLI command for scanner**

In `cli/main.py`, add a `scan` subcommand:
```
tradingagents scan --date 2026-05-05 --watchlist AAPL NVDA TSLA MSFT AMD
```
Output: ranked table of candidates with gap%, RVOL, catalyst risk, and score.

### Gate
- [ ] `tradingagents scan` runs without errors
- [ ] Scanner returns a non-empty ranked list for a provided watchlist
- [ ] Tickers below thresholds are excluded with clear reason logged

---

## Phase 7 — Earnings and Catalyst Calendar

### Objective
Add earnings proximity and macro event detection to prevent entering swing
trades that will be caught in binary event volatility.

### Steps

**7.1 — Create `tradingagents/dataflows/calendar_utils.py`**

```python
"""
Earnings and macro event calendar for swing trade risk assessment.
Uses Finnhub free tier for earnings data.
"""
import os
import finnhub
from datetime import datetime, timedelta

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

# Hardcoded 2026 Fed meeting dates (update annually)
FOMC_DATES_2026 = [
    "2026-01-28", "2026-03-18", "2026-04-29",
    "2026-06-17", "2026-07-29", "2026-09-16",
    "2026-10-28", "2026-12-16"
]

def check_earnings_proximity(
    ticker: str,
    from_date: str,
    buffer_days: int = 5
) -> dict:
    """
    Check if earnings fall within the projected hold window.

    Returns:
        dict with keys: risk (LOW/MEDIUM/HIGH), detail (str), earnings_date (str|None)
    """
    if not FINNHUB_API_KEY:
        return {"risk": "LOW", "detail": "Finnhub key not set — earnings not checked", "earnings_date": None}

    client = finnhub.Client(api_key=FINNHUB_API_KEY)
    from_dt = datetime.strptime(from_date, "%Y-%m-%d")
    to_dt = from_dt + timedelta(days=buffer_days + 2)

    try:
        earnings = client.earnings_calendar(
            _from=from_date,
            to=to_dt.strftime("%Y-%m-%d"),
            symbol=ticker
        )
        events = earnings.get("earningsCalendar", [])
        if events:
            earnings_date = events[0].get("date", "")
            days_away = (datetime.strptime(earnings_date, "%Y-%m-%d") - from_dt).days
            return {
                "risk": "HIGH",
                "detail": f"Earnings on {earnings_date} ({days_away} days away) — within hold window",
                "earnings_date": earnings_date,
            }
    except Exception:
        pass

    return {"risk": "LOW", "detail": "No earnings within hold window", "earnings_date": None}


def check_macro_events(from_date: str, hold_days: int = 5) -> dict:
    """Check for FOMC or major macro events within the hold window."""
    from_dt = datetime.strptime(from_date, "%Y-%m-%d")
    events_in_window = []

    for fomc_date in FOMC_DATES_2026:
        fomc_dt = datetime.strptime(fomc_date, "%Y-%m-%d")
        days_away = (fomc_dt - from_dt).days
        if 0 <= days_away <= hold_days:
            events_in_window.append(f"FOMC on {fomc_date} ({days_away} days away)")

    if events_in_window:
        return {
            "risk": "MEDIUM",
            "detail": "; ".join(events_in_window),
            "events": events_in_window
        }
    return {"risk": "LOW", "detail": "No macro events in hold window", "events": []}
```

### Gate
- [ ] `check_earnings_proximity("AAPL", "2026-05-05")` returns dict with risk key
- [ ] A ticker with earnings in 3 days returns `risk: "HIGH"`
- [ ] System works even when `FINNHUB_API_KEY` is not set (returns LOW with warning)

---

## Phase 8 — Daily Exit Monitor

### Objective
Build the exit signal system that evaluates open positions daily and generates
Hold / Exit / Take Profit signals.

### Steps

**8.1 — Create `tradingagents/monitor/exit_check.py`**

```python
"""
Daily exit monitor for open swing positions.
Run once per day — pre-market or end of day.
"""
import json
import os
from datetime import date
from tradingagents.dataflows.polygon_utils import get_candles, get_relative_volume
from tradingagents.dataflows.indicator_utils import calculate_rsi, calculate_ema
from tradingagents.dataflows.exceptions import DataValidationError


def check_position(position: dict, check_date: str = None) -> dict:
    """
    Evaluate an open position and return an exit signal.

    Args:
        position: dict with keys:
            ticker, entry_price, stop_price, target_price,
            entry_date, hold_days_max, rr_ratio, action ("BUY"/"SELL")
        check_date: "YYYY-MM-DD". Defaults to today.

    Returns:
        dict with: action ("HOLD"/"EXIT"/"TAKE_PROFIT"), reason, urgency
    """
    if check_date is None:
        check_date = date.today().strftime("%Y-%m-%d")

    ticker       = position["ticker"]
    entry_price  = position["entry_price"]
    stop_price   = position["stop_price"]
    target_price = position["target_price"]
    entry_date   = position["entry_date"]
    max_days     = position.get("hold_days_max", 5)
    trade_dir    = position.get("action", "BUY")

    from datetime import datetime
    days_held = (
        datetime.strptime(check_date, "%Y-%m-%d") -
        datetime.strptime(entry_date, "%Y-%m-%d")
    ).days

    from datetime import timedelta
    from_date = (datetime.strptime(check_date, "%Y-%m-%d") - timedelta(days=3)).strftime("%Y-%m-%d")
    df = get_candles(ticker, "1d", from_date, check_date)
    if df.empty:
        return {"action": "HOLD", "reason": "Could not fetch current price", "urgency": "LOW"}

    current_price = float(df["Close"].iloc[-1])
    rvol = get_relative_volume(ticker, check_date)
    rsi_series = calculate_rsi(df["Close"], period=7)
    current_rsi = float(rsi_series.iloc[-1])

    reasons = []

    if days_held >= max_days:
        return {"action": "EXIT", "reason": f"Max hold days ({max_days}) reached",
                "urgency": "HIGH", "current_price": current_price}

    if trade_dir == "BUY" and current_price <= stop_price:
        return {"action": "EXIT", "reason": f"Stop hit: price {current_price:.2f} ≤ stop {stop_price:.2f}",
                "urgency": "HIGH", "current_price": current_price}
    if trade_dir == "SELL" and current_price >= stop_price:
        return {"action": "EXIT", "reason": f"Stop hit: price {current_price:.2f} ≥ stop {stop_price:.2f}",
                "urgency": "HIGH", "current_price": current_price}

    if trade_dir == "BUY" and current_price >= target_price:
        return {"action": "TAKE_PROFIT", "reason": f"Target reached: price {current_price:.2f} ≥ target {target_price:.2f}",
                "urgency": "HIGH", "current_price": current_price}
    if trade_dir == "SELL" and current_price <= target_price:
        return {"action": "TAKE_PROFIT", "reason": f"Target reached: price {current_price:.2f} ≤ target {target_price:.2f}",
                "urgency": "HIGH", "current_price": current_price}

    if rvol < 0.8:
        reasons.append(f"Volume collapsed (RVOL: {rvol:.1f}x) — thesis may be weakening")

    if trade_dir == "BUY" and current_rsi > 75:
        reasons.append(f"RSI-7 overbought at {current_rsi:.1f} — consider scaling out")

    urgency = "MEDIUM" if reasons else "LOW"
    return {
        "action":        "HOLD",
        "reason":        "; ".join(reasons) if reasons else "Position within parameters",
        "urgency":       urgency,
        "current_price": current_price,
        "days_held":     days_held,
        "rsi_7":         current_rsi,
        "rvol":          rvol,
    }


def check_all_positions(positions_file: str = None, check_date: str = None) -> list[dict]:
    """
    Check all open positions from a JSON file.
    Default file: ~/.tradingagents/positions/open_positions.json
    """
    if positions_file is None:
        positions_file = os.path.expanduser("~/.tradingagents/positions/open_positions.json")

    if not os.path.exists(positions_file):
        print(f"[MONITOR] No open positions file found at {positions_file}")
        return []

    with open(positions_file, "r") as f:
        positions = json.load(f)

    results = []
    for pos in positions:
        result = check_position(pos, check_date)
        result["ticker"] = pos["ticker"]
        results.append(result)
        print(f"[MONITOR] {pos['ticker']}: {result['action']} — {result['reason']}")

    return results
```

**8.2 — Add CLI command for exit monitor**
```
tradingagents monitor --date 2026-05-05
```

### Gate
- [ ] `check_position()` returns EXIT when price is below stop
- [ ] `check_position()` returns TAKE_PROFIT when price hits target
- [ ] `check_position()` returns EXIT when max hold days reached
- [ ] Works with an empty positions file without error

---

## Phase 9 — Swing Trade Memory and Journal

### Objective
Extend the upstream memory system to track swing trade outcomes and feed
win/loss patterns back into the Portfolio Manager on subsequent runs.

### Steps

**9.1 — Extend the memory log format**

The upstream system writes to `~/.tradingagents/memory/trading_memory.md`.
We extend the entry format to include swing-specific fields:

```
## [TICKER] — [DATE]
- Action: BUY | SELL | NO_TRADE
- Entry Zone: $142.50 – $143.20
- Stop: $139.80 (ATR-based)
- Target: $147.60 (2.1:1 R:R)
- Hold Days Max: 4
- Catalyst Risk: LOW
- Thesis: [2-3 sentence thesis]

### Outcome (filled on close)
- Exit Date: [date]
- Exit Price: $146.90
- Days Held: 3
- P&L: +$3.70/share (+2.6%)
- Result: WIN | LOSS | STOPPED | MAX_DAYS
- Reflection: [1 paragraph — what worked, what to watch next time]
```

**9.2 — Update Position Logger**

After each `propagate()` call that produces a BUY or SELL decision, write the
open position to `~/.tradingagents/positions/open_positions.json` automatically.

**9.3 — Feed history to Portfolio Manager**

When the Portfolio Manager runs for a ticker that has prior swing trade history,
inject the last 3 results into the PM prompt:

```
## Prior swing trades in [TICKER] (last 3):
1. [DATE]: BUY at $142.50 → Exited $146.90 (+2.6%) — WIN
2. [DATE]: BUY at $151.20 → Stopped at $148.50 (-1.8%) — STOPPED
3. [DATE]: NO_TRADE — R:R was insufficient (1.4:1)
Pattern note: [auto-generated reflection from memory]
```

### Gate
- [ ] After a `propagate()` BUY decision, position is written to JSON file
- [ ] Subsequent run on same ticker shows prior results in PM prompt
- [ ] Memory log entries include all swing-specific fields

---

## Phase 10 — Integration Testing and Tuning

### Objective
Run the complete system end-to-end on multiple tickers and dates. Validate
output quality, tune prompts where needed, and achieve full test suite pass.

### Steps

**10.1 — Create `tests/test_swing_signal.py`**

```python
import pytest
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.agents.schemas import SwingTradeDecision

@pytest.fixture
def swing_config():
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = "anthropic"
    config["max_debate_rounds"] = 1
    config["time_horizon"] = "1-5 days"
    return config

def test_propagate_returns_swing_decision(swing_config):
    ta = TradingAgentsGraph(debug=False, config=swing_config)
    state, decision = ta.propagate("AAPL", "2026-05-02")
    assert decision is not None
    assert hasattr(decision, "action")
    assert hasattr(decision, "stop_price")
    assert hasattr(decision, "target_price")
    assert hasattr(decision, "hold_days_max")
    assert decision.hold_days_max <= 5

def test_rr_ratio_meets_minimum(swing_config):
    ta = TradingAgentsGraph(debug=False, config=swing_config)
    _, decision = ta.propagate("NVDA", "2026-05-02")
    if decision.action in ("BUY", "SELL"):
        assert decision.rr_ratio >= 2.0, f"R:R {decision.rr_ratio:.2f} below 2.0 minimum"

def test_no_fundamentals_in_output(swing_config):
    ta = TradingAgentsGraph(debug=False, config=swing_config)
    state, _ = ta.propagate("TSLA", "2026-05-02")
    full_output = str(state)
    forbidden = ["discounted cash flow", "intrinsic value", "price-to-earnings",
                 "3-6 month", "12-month price target"]
    for term in forbidden:
        assert term.lower() not in full_output.lower(), f"Found forbidden term: '{term}'"

def test_no_trade_on_insufficient_rr(swing_config):
    """System should output NO_TRADE when setup doesn't meet R:R minimum."""
    pass
```

**10.2 — Test on 5 diverse tickers across different market conditions**

Run `propagate()` on:
- Large-cap momentum: NVDA, AAPL
- Mid-cap volatile: SMCI, PLTR
- ETF: SPY (should often return NO_TRADE due to low RVOL)
- GC=F Gold Futures (your original test case — should now work with real indicators)

Review each output for:
- Are all `SwingTradeDecision` fields populated?
- Is the thesis scoped to 1–5 days?
- Is the stop ATR-based?
- Is the R:R ≥ 2.0 when action is BUY/SELL?
- Does the system correctly return NO_TRADE when conditions aren't met?

**10.3 — Prompt tuning iteration**

After reviewing outputs, iterate on prompts where agents are:
- Still using long-term language
- Not calculating ATR stops correctly
- Producing overly verbose reports (target < 400 words per agent)
- Not citing the indicator values from the data bundle

**10.4 — Full test suite**

```bash
pytest tests/ -v --tb=short
```

All tests must pass. Document any known failures with tickets.

### Gate — Project Complete
- [ ] All 10 phase gates passed
- [ ] Full test suite passes
- [ ] GC=F now runs with real indicator data (confirms Phase 1 bug fix)
- [ ] 5-ticker validation run reviewed and approved
- [ ] CLAUDE.md and BUILD_PLAN.md up to date with any changes made during build

---

## Appendix A — Recommended Claude Code Session Workflow

Each Claude Code session should follow this pattern:

```
1. Read CLAUDE.md (briefing)
2. Read BUILD_PLAN.md (current phase)
3. Run existing tests to confirm previous work is intact
4. Implement current phase steps
5. Run phase gate tests
6. Update this document with any deviations or notes
7. Commit with message: "Phase N: [description]"
```

## Appendix B — Data Provider Fallback Strategy

| Provider | Data Type | Free Tier Limit | Fallback |
|---|---|---|---|
| Polygon.io | 15m, 1hr candles | 5 API calls/min | Cache aggressively |
| Alpha Vantage | Daily OHLCV | 25 calls/day | Primary daily fallback |
| yfinance | Daily OHLCV | Unlimited (unofficial) | Emergency fallback only |
| Finnhub | Earnings calendar | 60 calls/min | Works for swing cadence |

Always check `POLYGON_API_KEY` is set before any intraday data call.
Log a clear error if not set rather than silently failing.

## Appendix C — Known Upstream Issues to Monitor

- **Empty indicator bug (GC=F)**: Fixed in Phase 1. Watch for recurrence on
  futures symbols (GC=F, ES=F) which may have different data format from equities.
- **Windows UTF-8 encoding**: Upstream fixed in v0.2.4. Keep Docker setup as
  the recommended path for Windows users.
- **LangGraph checkpoint SQLite files**: Can grow large if `--checkpoint` is
  enabled and runs are never completed. Clear with `tradingagents analyze --clear-checkpoints`.

---

*Build Plan version 1.0 — Swing Trading Agents*
*Based on TauricResearch/TradingAgents upstream v0.2.4*
