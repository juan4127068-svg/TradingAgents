## SECURITY POLICY — Read Before Any Git or GitHub Operation

> This applies to every session, every command, every push.

```
PRIVATE FORK — ALL WORK STAYS LOCAL AND IN OUR PRIVATE REPO ONLY.

origin   = YOUR_USERNAME/swing-trading-agents  ← push here ONLY
upstream = TauricResearch/TradingAgents         ← READ ONLY, never push

PUSH LOCK — run once, enforced permanently:
  git remote set-url --push upstream DISABLED

FORBIDDEN — no exceptions:
  git push upstream <anything>
  gh pr create --repo TauricResearch/TradingAgents
  gh issue create --repo TauricResearch/TradingAgents
  Any write operation to any public repository

VERIFY before every session:
  git remote get-url --push upstream   # must print: DISABLED
```

Full rules are in `GIT_POLICY.md` — read it before any git operation.

---

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (using uv is preferred, pip also works)
uv pip install -e .
pip install -e .

# Run the CLI (interactive: prompts for ticker, date, provider, analysts)
tradingagents analyze

# Run with checkpoint/resume support
tradingagents analyze --checkpoint

# Clear saved checkpoints before running
tradingagents analyze --clear-checkpoints

# Run all tests
pytest tests/

# Run a single test file
pytest tests/test_structured_agents.py -v

# Run tests by marker
pytest tests/ -m unit
pytest tests/ -m smoke

# Run the smoke test for structured-output agents across providers
python scripts/smoke_structured_output.py
```

```bash
# ── Swing trade commands (added in this fork) ────────────────────────────────

# Pre-market watchlist scanner — run before 9:30 AM ET (Phase 6)
tradingagents scan --date 2026-05-05 --watchlist AAPL NVDA TSLA MSFT AMD

# Daily exit monitor for open positions (Phase 8)
tradingagents monitor --date 2026-05-05

# Swing-specific test gates (must pass before each phase advances)
pytest tests/test_data_pipeline.py -v    # Phase 1 gate
pytest tests/test_indicators.py -v       # Phase 3 gate
pytest tests/test_swing_signal.py -v     # Phase 10 gate
pytest tests/ -v --tb=short              # Full suite
```

## Architecture

TradingAgents is a LangGraph-based multi-agent framework where specialized LLM agents collaborate on a trading decision. The pipeline flows:

**Analysts (sequential)** → **Researchers (Bull/Bear debate)** → **Research Manager** → **Trader** → **Risk Mgmt (3-way debate)** → **Portfolio Manager** → final 5-tier rating signal

The main entry point is `TradingAgentsGraph` in `tradingagents/graph/trading_graph.py`. Call `.propagate(company_name, trade_date)` — it returns a tuple `(final_state, signal)` where `signal` is the extracted rating string (`.process_signal()` is called internally). Analyst selection is controlled by the `selected_analysts` list passed to `TradingAgentsGraph.__init__()`, not via config. The graph is assembled dynamically in `graph/setup.py` from that list.

### Key subsystems

**`tradingagents/graph/`** — LangGraph orchestration
- `trading_graph.py`: `TradingAgentsGraph` class — key public method: `propagate(company_name, trade_date)` returns `(final_state, signal)`; `process_signal` is called internally
- `setup.py`: Constructs the StateGraph with nodes/edges per analyst selection
- `conditional_logic.py`: Routing between debate rounds (configurable rounds per debate)
- `checkpointer.py`: SQLite-backed resume via `langgraph-checkpoint-sqlite`; checkpoints stored at `~/.tradingagents/cache/checkpoints/<TICKER>.db`
- `reflection.py`: Post-trade LLM reflection injected into memory on next run

**`tradingagents/agents/`** — All agent implementations
- `analysts/`: One file per analyst type (market, social_media, news, fundamentals) — each wraps LLM calls with tool nodes
- `managers/`: `research_manager.py` and `portfolio_manager.py` use **structured output** (Pydantic schemas in `agents/schemas.py`)
- `traders/trader.py`: Also structured output
- `risk_mgmt/`: Three debators (aggressive/neutral/conservative) in a round-robin loop
- `utils/memory.py`: Reads the persistent `trading_memory.md` log; injects prior decisions into agent context

**`tradingagents/llm_clients/`** — Multi-provider LLM abstraction
- `factory.py`: `create_llm_client(provider, model, **kwargs)` → `BaseLLMClient`
- Provider clients (`openai_client.py`, `anthropic_client.py`, `google_client.py`, `azure_client.py`) each expose `.get_llm()` returning a LangChain chat model
- `openai_client.py` also handles xAI, DeepSeek, Qwen, GLM, OpenRouter (all OpenAI-compatible APIs)
- `model_catalog.py`: Canonical model name lists per provider

**`tradingagents/dataflows/`** — Market data
- `interface.py`: Vendor-agnostic routing (yfinance or Alpha Vantage) via `TOOLS_CATEGORIES` dispatch
- Data is cached under `~/.tradingagents/cache/`

**`tradingagents/default_config.py`** — All tuneable defaults: provider, models, debate rounds, data vendor, checkpoint on/off, output language

**`cli/main.py`** — Typer app; wraps `TradingAgentsGraph` with rich terminal UI, interactive provider/model selection, and a `StatsCallbackHandler` that tracks token usage

### Persistent state

| Path | Purpose |
|------|---------|
| `~/.tradingagents/memory/trading_memory.md` | Append-only decision log; past entries are injected into analyst context |
| `~/.tradingagents/cache/checkpoints/<TICKER>.db` | LangGraph SQLite checkpoint for mid-run resume |
| `~/.tradingagents/logs/` | Saved full analysis reports |

### Structured output pattern

Decision agents (Research Manager, Trader, Portfolio Manager) use `with_structured_output(Schema)` on the LangChain model. Schemas are Pydantic models in `tradingagents/agents/schemas.py`:
- `PortfolioRating` (5-tier): Buy / Overweight / Hold / Underweight / Sell — used by Research Manager and Portfolio Manager
- `TraderAction` (3-tier): Buy / Hold / Sell — used by the Trader
- `ResearchPlan`, `TraderProposal` — wrapping types that include the rating/action plus reasoning

The `utils/agent_utils.py` helpers handle provider-specific quirks (e.g., DeepSeek reasoning stripping).

### Adding a new LLM provider

1. Add a client class in `tradingagents/llm_clients/` implementing `BaseLLMClient`
2. Register it in `factory.py`
3. Add model names to `model_catalog.py`
4. Add the API key var to `.env.example`

### Environment

Copy `.env.example` to `.env` and populate whichever provider keys you need. Azure OpenAI uses `.env.enterprise.example` instead. The framework raises an early error if a required key is missing for the chosen provider.

---
---

# SWING TRADING BUILD OVERLAY
## Briefing Document for Claude Code Engineer

> **Read the upstream section above first** — it describes the real class names,
> method signatures, and file paths of the codebase as it exists right now.
> Everything below describes what we are building on top of it and why.
> When the two sections conflict, the upstream section has the ground truth on
> *what exists*; this section has the authority on *what to build*.

---

## 1. Project Identity

| Field | Value |
|---|---|
| **Project name** | swing-trading-agents |
| **Upstream** | `git remote upstream` → TauricResearch/TradingAgents |
| **Purpose** | Multi-agent LLM system that generates 1–5 day swing trade signals |
| **Target user** | A single trader running this daily pre-market or post-close |
| **NOT for** | Intraday scalping, position trades, automated execution |
| **Research disclaimer** | This system is for research and educational use only. Not financial advice. |

---

## 2. Hard Constraints — Never Violate These

These constraints define what "done" means for this project. Every agent prompt,
every config value, every output schema must respect these rules:

```
MAX_HOLD_DAYS         = 5          # trading days — hard cap, no exceptions
MIN_RISK_REWARD       = 2.0        # minimum 2:1 R:R or signal is rejected
STOP_METHOD           = "atr"      # always ATR-based, never MA-based
ATR_STOP_MULTIPLIER   = 1.5        # stop = entry - (1.5 × ATR-14)
MAX_RISK_PER_TRADE    = 0.02       # 2% of account maximum per trade
CANDLE_TIMEFRAMES     = ["15m", "1hr", "1d"]   # multi-timeframe required
PRIMARY_ENTRY_TF      = "1hr"      # entry signals from 1hr chart
TREND_FILTER_TF       = "1d"       # daily chart determines direction only
MIN_RELATIVE_VOLUME   = 1.5        # rvol vs 20-day avg — minimum for entry
DEBATE_ROUNDS         = 1          # max_debate_rounds = 1, never higher
```

---

## 3. Repository Structure

The upstream section already documents existing files accurately. This section
shows only the **delta** — what is new, modified, or removed in this fork.

```
swing-trading-agents/
├── CLAUDE.md                          ← this file (merged upstream + overlay)
├── BUILD_PLAN.md                      ← phase-by-phase build plan (read next)
│
├── tradingagents/
│   ├── default_config.py              ← MODIFIED — swing config keys added
│   │
│   ├── agents/
│   │   ├── analysts/
│   │   │   ├── market_analyst.py      ← MODIFIED — swing indicator prompts
│   │   │   ├── news_analyst.py        ← MODIFIED — catalyst focus
│   │   │   ├── social_media_analyst.py← KEEP — minimal changes needed
│   │   │   └── fundamentals_analyst.py← REPURPOSED → catalyst_analyst.py
│   │   ├── managers/
│   │   │   ├── research_manager.py    ← MODIFIED — swing-scoped synthesis
│   │   │   └── portfolio_manager.py   ← MODIFIED — SwingTradeDecision schema
│   │   ├── traders/
│   │   │   └── trader.py              ← MODIFIED — ATR stops, R:R filter
│   │   ├── risk_mgmt/                 ← MODIFIED — short-term vol weighting
│   │   └── schemas.py                 ← EXTENDED — SwingTradeDecision added
│   │
│   ├── dataflows/
│   │   ├── interface.py               ← MODIFIED — multi-TF data routing
│   │   ├── yfin_utils.py              ← MODIFIED — fix indicator bug
│   │   ├── polygon_utils.py           ← NEW — Polygon.io candle fetcher
│   │   ├── indicator_utils.py         ← NEW — swing indicator calculations
│   │   ├── calendar_utils.py          ← NEW — earnings/event calendar
│   │   └── exceptions.py              ← NEW — DataValidationError
│   │
│   ├── graph/
│   │   ├── trading_graph.py           ← MODIFIED — fundamentals node removed
│   │   └── setup.py                   ← MODIFIED — swing_config injected
│   │
│   ├── scanners/                      ← NEW directory
│   │   ├── __init__.py
│   │   └── premarket.py               ← NEW — gap/volume watchlist scanner
│   │
│   └── monitor/                       ← NEW directory
│       ├── __init__.py
│       └── exit_check.py              ← NEW — daily position exit evaluator
│
├── cli/
│   └── main.py                        ← MODIFIED — scan + monitor subcommands
│
├── prompts/                           ← NEW directory — all system prompts
│   ├── market_analyst_swing.md
│   ├── catalyst_analyst_swing.md
│   ├── bull_researcher_swing.md
│   ├── bear_researcher_swing.md
│   ├── trader_swing.md
│   └── portfolio_manager_swing.md
│
└── tests/
    ├── test_structured_agents.py      ← KEEP upstream tests passing
    ├── test_data_pipeline.py          ← NEW — Phase 1 gate
    ├── test_indicators.py             ← NEW — Phase 3 gate
    └── test_swing_signal.py           ← NEW — Phase 10 gate
```

---

## 4. What Changed From Upstream (Delta Summary)

### Removed
- **Fundamentals Analyst** (DCF, P/E, intrinsic value) — irrelevant for 1–5 day holds.
  The node is removed from `setup.py`. The file is repurposed as `catalyst_analyst.py`.
- **200-day MA** as stop-loss anchor — replaced by ATR-14
- **3–6 month time horizon** language in all prompts
- **MACD 12/26/9** — too slow for swing setups
- **VWMA** — replaced by VWAP

### Modified
- All agent system prompts scoped to 1–5 day horizon (loaded from `prompts/` directory)
- `max_debate_rounds` set to 1 in `default_config.py`
- `schemas.py` extended with `SwingTradeDecision` (upstream schemas kept intact)
- Trader agent requires 2:1 R:R minimum before outputting Buy/Sell
- Technical analyst uses swing-appropriate indicator set (see Section 6)

### Added
- `polygon_utils.py` — multi-timeframe candle fetcher (15m, 1hr, 1d)
- `indicator_utils.py` — ATR, RVOL, ADX, VWAP, EMA crossover calculations
- `exceptions.py` — `DataValidationError`, `InsufficientDataError`
- `calendar_utils.py` — earnings date proximity + FOMC event check
- `scanners/premarket.py` — morning watchlist generator
- `monitor/exit_check.py` — daily open position evaluator
- `prompts/` directory — all agent system prompts stored as markdown files

### Kept Unchanged (do not touch)
- `tradingagents/llm_clients/` — all LLM provider adapters
- `.propagate()` and `.process_signal()` public API signatures
- Memory/checkpoint system core logic
- Docker configuration
- All upstream tests (must continue passing)

---

## 5. Upstream Compatibility Rules

We maintain upstream compatibility so we can `git pull upstream main` for bug fixes.

- Never modify `tradingagents/llm_clients/` — pull from upstream freely
- Never modify `tradingagents/graph/` node *signatures* — only add or remove nodes
- Never modify the `.propagate(company_name, trade_date)` signature
- Config additions are **additive only** — never remove or rename existing keys
- New files go in new directories (`scanners/`, `monitor/`, `prompts/`)
- When upstream releases an update, review the changelog before merging

---

## 6. Swing Trade Indicator Reference

This table is the authoritative source for what the Market Analyst agent uses.
Do not add or remove indicators without updating this table.

| Indicator | Settings | Timeframe | Purpose |
|---|---|---|---|
| EMA | 8-period | 1hr | Short-term momentum |
| EMA | 21-period | 1hr | Swing momentum filter |
| EMA crossover | 8 > 21 = bullish | 1hr | Entry trigger |
| VWAP | Session reset daily | 1hr | Price anchor / fair value |
| RSI | 7-period | 1hr | Overbought/oversold (faster signal) |
| ATR | 14-period | 1d | Stop-loss sizing |
| ADX | 14-period | 1d | Trend strength (>25 = trending) |
| Relative Volume | vs 20d average | 1d | Participation confirmation |
| Support/Resistance | Swing highs/lows last 10d | 1d | Target and stop levels |
| Daily trend | EMA 20 slope | 1d | Direction filter only |

**All 5 entry conditions must be checked before signalling a trade:**
1. Daily trend is up — price above EMA-20, ADX > 20
2. 1hr EMA-8 has crossed above EMA-21
3. Price is above VWAP
4. RSI-7 is between 40–70 (not extended at entry)
5. Relative volume ≥ 1.5× 20-day average

---

## 7. Stop-Loss and Position Sizing Logic

The Trader agent and Portfolio Manager must use this exact logic.
Do not use percentage-based stops or MA-based stops.

```python
# Stop loss calculation
atr_14 = get_atr(symbol, period=14, timeframe="1d")
stop_price = entry_price - (ATR_STOP_MULTIPLIER * atr_14)   # 1.5 × ATR

# Target calculation — minimum 2:1 R:R
risk_per_share = entry_price - stop_price
min_target     = entry_price + (MIN_RISK_REWARD * risk_per_share)

# Position sizing — account-based (2% risk per trade)
account_size      = config["account_size"]
max_risk_dollars  = account_size * MAX_RISK_PER_TRADE
shares            = max_risk_dollars / risk_per_share

# Signal validity gate — reject if R:R not met
if (target_price - entry_price) / risk_per_share < MIN_RISK_REWARD:
    return {"action": "NO_TRADE", "reason": "Insufficient R:R ratio"}
```

---

## 8. SwingTradeDecision Output Schema

This Pydantic model lives in `tradingagents/agents/schemas.py` alongside the
upstream `PortfolioRating` and `TraderAction` schemas. Do not remove upstream schemas.

```python
class SwingTradeDecision(BaseModel):
    # ── Upstream-compatible fields ──────────────────────────────────────────
    action:     Literal["BUY", "SELL", "HOLD", "NO_TRADE"]
    rating:     Literal["Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"]
    confidence: float                      # 0.0 – 1.0

    # ── Swing trade execution fields ────────────────────────────────────────
    entry_zone_low:     float              # lower bound of entry zone
    entry_zone_high:    float              # upper bound of entry zone
    stop_price:         float              # ATR-based stop
    target_price:       float              # minimum 2:1 R:R target
    atr_used:           float              # raw ATR-14 value used
    rr_ratio:           float              # (target - entry) / (entry - stop)

    # ── Hold parameters ─────────────────────────────────────────────────────
    hold_days_max:      int                # hard cap: never > 5
    hold_days_expected: int                # estimated hold based on setup

    # ── Risk and catalyst ───────────────────────────────────────────────────
    catalyst_risk:      Literal["LOW", "MEDIUM", "HIGH"]
    catalyst_detail:    str                # e.g. "Earnings in 3 days — HIGH risk"

    # ── Thesis ──────────────────────────────────────────────────────────────
    thesis_summary:      str               # 2–3 sentences max
    invalidation_signal: str               # what proves the trade wrong early
    exit_conditions:     list[str]         # ["Stop hit", "Target hit", "Day 5 forced exit"]
```

---

## 9. Data Pipeline Architecture

```
propagate("AAPL", "2026-05-05")
          │
          ▼
   interface.py  (DataRouter)
          │
    ┌─────┴──────────────────────┐
    │                            │
    ▼                            ▼
Daily candles (1d)        Intraday candles (1hr, 15m)
yfinance / Alpha Vantage   Polygon.io
[existing yfin_utils.py]   [new polygon_utils.py]
    │                            │
    └─────────────┬──────────────┘
                  ▼
         indicator_utils.py
         Calculates: ATR, RSI-7, ADX, VWAP,
         EMA 8/21, RVOL, EMA-20 daily.
         Raises DataValidationError if any
         indicator returns None or empty.
                  │
                  ▼
         calendar_utils.py
         Checks: earnings within hold window,
         FOMC/CPI dates. Sets catalyst_risk.
                  │
                  ▼
         Analyst Team receives
         pre-computed, validated data bundle
```

**Critical:** Silent indicator failure caused the empty MACD/RSI/VWMA bug in
the original GC=F report. `indicator_utils.py` must raise a hard
`DataValidationError` — never return None or allow the run to continue with
blank data. Fix this in Phase 1 before any other changes.

---

## 10. LangGraph Node Order (Swing Fork)

Upstream `setup.py` assembles the graph dynamically. In this fork the node
order is:

```
[Data Fetch + Validation]          ← interface.py + indicator_utils.py
        ↓
[Parallel analysts]
   Market Analyst                  ← MODIFIED prompt
   Catalyst Analyst                ← RENAMED from fundamentals_analyst
   News Analyst                    ← MODIFIED prompt
   Sentiment/Social Analyst        ← KEEP as-is
        ↓
[Research Manager]                 ← MODIFIED — swing synthesis
        ↓
[Bull Researcher | Bear Researcher]← 1 debate round only
        ↓
[Trader Agent]                     ← ATR stop + R:R gate
        ↓
[Risk Management]                  ← 1 round only
        ↓
[Portfolio Manager]                ← outputs SwingTradeDecision
        ↓
[Decision Logger]                  ← extends existing memory system
```

**Removed from graph:** Fundamentals Analyst node. Remove its node registration
in `setup.py` and any edges that reference it.
**Injected into all nodes:** `swing_config` dict added to initial graph state.

---

## 11. New Files to Build

### `tradingagents/dataflows/exceptions.py`
`DataValidationError(ticker, field, message)` and `InsufficientDataError`.
Build first — everything else imports from here.

### `tradingagents/dataflows/polygon_utils.py`
Polygon.io REST client for multi-timeframe candles.
Functions: `get_candles(ticker, timeframe, from_date, to_date)`,
`get_relative_volume(ticker, date, lookback_days=20)`,
`get_session_vwap(ticker, date)`.
Full implementation is in `BUILD_PLAN.md` Phase 1.

### `tradingagents/dataflows/indicator_utils.py`
Pure calculation functions: `calculate_atr`, `calculate_rsi`, `calculate_adx`,
`detect_ema_crossover`, `build_swing_indicator_bundle`.
All functions raise `DataValidationError` instead of returning None.
Full implementation is in `BUILD_PLAN.md` Phase 1.

### `tradingagents/dataflows/calendar_utils.py`
`check_earnings_proximity(ticker, from_date, buffer_days)` via Finnhub free tier.
`check_macro_events(from_date, hold_days)` via hardcoded FOMC calendar.
Degrades gracefully when `FINNHUB_API_KEY` is not set.

### `tradingagents/scanners/premarket.py`
Run 8:00–9:30 AM ET. Filter criteria: gap > 2%, RVOL > 1.5×, price $5–$500,
avg daily volume > 500k, no earnings within 5 days.
Output: ranked list of 3–5 tickers for `propagate()` loop.

### `tradingagents/monitor/exit_check.py`
Run end-of-day or pre-market. Per open position: check stop breach, target hit,
max hold days exceeded, volume collapse (RVOL < 0.8×), RSI overbought warning.
Output: `{"ticker", "action": HOLD|EXIT|TAKE_PROFIT, "reason", "urgency"}`.

---

## 12. Environment Variables

```bash
# ── LLM provider (at least one required) ────────────────────────────────────
ANTHROPIC_API_KEY=...       # Claude — recommended for this project
OPENAI_API_KEY=...          # Optional fallback

# ── Market data ─────────────────────────────────────────────────────────────
POLYGON_API_KEY=...         # Polygon.io — required for intraday candles
ALPHA_VANTAGE_API_KEY=...   # Keep — daily data fallback

# ── Optional ────────────────────────────────────────────────────────────────
FINNHUB_API_KEY=...         # Earnings calendar (free tier at finnhub.io)

# ── Swing trade config (override default_config.py values) ──────────────────
SWING_ACCOUNT_SIZE=25000    # Account size for position sizing
SWING_MAX_RISK_PCT=0.02     # 2% per trade
```

---

## 13. Testing Requirements

Phase gates must pass in order. Never start Phase N+1 until Phase N gate passes.

```bash
pytest tests/test_data_pipeline.py -v    # Phase 1 gate — data + validation
pytest tests/test_indicators.py -v       # Phase 3 gate — indicator calculations
pytest tests/test_swing_signal.py -v     # Phase 10 gate — end-to-end signal

pytest tests/ -v --tb=short              # Full suite — run before any commit
```

Upstream tests in `tests/test_structured_agents.py` must continue to pass
throughout all phases. Never break them.

---

## 14. What NOT to Change

```
tradingagents/llm_clients/          — all LLM provider adapters
.propagate() signature              — public API must not change
.process_signal() signature         — public API must not change
tradingagents/default_config.py     — only ADD keys, never rename/remove
Docker configuration                — do not touch
~/.tradingagents/ checkpoint logic  — extend only, never replace
tests/test_structured_agents.py     — must stay green throughout
```

---

## 15. Session Startup Checklist

When starting a new Claude Code session, verify before writing any code:

- [ ] You are in the `swing-trading-agents/` directory (not the upstream clone)
- [ ] `POLYGON_API_KEY` is set in `.env`
- [ ] `ANTHROPIC_API_KEY` or another LLM key is set in `.env`
- [ ] `BUILD_PLAN.md` is open — identify the current phase
- [ ] Previous phase gate tests are passing
- [ ] `git log --oneline -5` reviewed — know what was last changed

---

*Upstream section: auto-generated by Claude Code on repo init*
*Swing overlay: Swing Trading Agents v0.1.0-dev — May 2026*
