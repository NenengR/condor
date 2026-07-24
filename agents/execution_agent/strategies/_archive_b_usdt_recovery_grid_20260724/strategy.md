---
name: B-USDT Recovery Grid
description: Loss-recovery grid for stranded B-USDT hedged position on gate_io_perpetual.
  Runs LONG-biased grid rungs to harvest range profit and reduce net loss.
agent_key: null
skills: []
default_config:
  frequency_sec: 120
  execution_mode: loop
  connector_name: gate_io_perpetual
  trading_pair: B-USDT
  size_from_live_balance: true
  leverage: 20
  recovery:
    short_entry: 0.18077
    short_qty: 480
    short_notional_usd: 86.8
    long_entry: 0.24342
    long_qty: 520
    long_notional_usd: 126.6
    short_leverage: 20
    short_is_cross_margin: true
    max_long_notional_usd: 174.0
    grid_step_pct: 0.015
    rung_notional_usd: 10.0
    take_profit_per_rung_pct: 0.02
    stop_loss_per_rung_pct: 0.05
    target_net_pnl_usd: 1.0
    max_added_loss_usd: 15.0
  risk_limits:
    max_open_executors: 6
    max_position_size_quote: 20
    shutdown_drawdown_pct: 40.0
default_trading_context: ''
created_by: 0
created_at: '2026-07-20T06:00:00+00:00'
---

## ABSOLUTE RULE: LONG ONLY — NEVER ADD TO THE SHORT

Your ONLY trade direction is **LONG (side=1)**. You will NEVER open side=2 or submit a SELL.
The SHORT 480 is the user's pre-existing loss. You only STACK LONG rungs against it.

## Position snapshot (as of 2026-07-22)

| Side | Qty | Entry | Notional |
|------|-----|-------|----------|
| SHORT | 480 | 0.18077 | ~$86.8 |
| LONG | 520 | 0.24342 | ~$126.6 |
| Net | -40 SHORT bias | | |

Your job: run LONG grid rungs to harvest range profit. When combined P&L >= +$1, declare recovered.

## P&L formula (pure math, no tool call)

```
short_pnl = (0.18077 - price) × 480
long_pnl  = (price - 0.24342) × 520 + executor_rungs_pnl
combined  = short_pnl + long_pnl
```

## Rails (pure math checks BEFORE any order)

1. **LONG only.** Never side=2.
2. **Exposure cap:** total LONG notional (user's $126 + your rungs) ≤ $174 (2× SHORT notional).
3. **Grid drawdown:** if grid-own P&L ≤ -$15 → stop new recovery rungs.
4. **Recovery target:** combined >= +$1 → wind-down.

## Each Tick — EXACTLY these steps

### Step 1: Get price (1 tool call)
```
get_market_data(action="prices", connector_name="gate_io_perpetual", trading_pairs=["B-USDT"])
```
If rate-limited (429), skip this tick — do NOT retry or investigate. Just journal "rate-limited" and return.

### Step 2: Compute P&L (NO tool call — pure math)
Read `[CORE DATA - executors]` for open rung P&L. Compute short_pnl, long_pnl, combined.

### Step 3: Check rails (NO tool call — pure math)
- combined >= +$1 → go to Wind-down
- grid drawdown <= -$15 → hold, no new rungs
- total LONG notional >= $174 → hold, at cap

### Step 4: Entry decision (NO tool call — pure math)
Count open rungs from `[CORE DATA - executors]`.

**Defense (price >= 0.30):** open LONG rung immediately.
**Recovery (normal):** open LONG rung ONLY when:
- No open rungs exist (first entry), OR
- price <= (lowest_open_rung_entry × 0.985) — fresh 1.5% dip

If neither condition met → hold (no new rung). Skip to Step 6.

### Step 5: Create rung (1 tool call — if entry decided)

**CRITICAL: `side` and `open_order_type` MUST be integers, not strings. The value 1 means BUY/MARKET.**

**⚠ HEDGE MODE — NO AUTO-TP/SL:** Do NOT put `take_profit` or `stop_loss` in `triple_barrier_config`.
On gate_io_perpetual HEDGE mode, the position_executor's auto-exit fires a plain SELL order
without `position_action: 'CLOSE'`, which opens a new SHORT instead of closing the LONG.
Use `time_limit` only as a safety net. Close rungs manually in Step 5b below.

```
manage_executors(action="create", executor_type="position_executor", controller_id="execution_agent.b_usdt_recovery_grid_7", executor_config={"connector_name": "gate_io_perpetual", "trading_pair": "B-USDT", "side": 1, "amount": RUNG_QTY, "leverage": 20, "triple_barrier_config": {"time_limit": 21600, "open_order_type": 1}})
```

Where `RUNG_QTY = 10.0 / current_price` (base amount for $10 notional rung).

**If create fails with a validation error about side/open_order_type being strings:**
Do NOT spend the rest of the tick investigating. Journal the error and move on.
Do NOT call `manage_executors(executor_type="position_executor")` to fetch the schema — you already know the schema. Just retry the create ONE more time with the exact same config. If it fails again, journal and wait for next tick.

### Step 5b: Close rungs at TP (each tick, for each open rung)

For each open rung from `[CORE DATA - executors]`:
- Compute unrealized PnL: `(current_price - rung_entry) / rung_entry`
- If PnL >= 0.02 (2% TP) OR PnL <= -0.05 (5% SL):
  1. Stop the executor (keep position open so the LONG stays on the book):
     ```
     manage_executors(action="stop", executor_id="{rung_id}", keep_position=true)
     ```
  2. Close the LONG with position_action CLOSE (reduces LONG, does NOT open SHORT):
     ```
     manage_executors(action="create", executor_type="order_executor", controller_id="execution_agent.b_usdt_recovery_grid_7", executor_config={"connector_name": "gate_io_perpetual", "trading_pair": "B-USDT", "side": 2, "amount": RUNG_QTY, "execution_strategy": "MARKET", "position_action": "CLOSE", "leverage": 20})
     ```
  3. Journal the close with reason (TP or SL) and PnL.

### Step 6: Journal (1 tool call)
```
trading_agent_journal_write(entry_type="action", text="tick#N: B@{price} | SHORT pnl ${x} | LONG pnl ${y} | combined ${z} | open_rungs={n} | {action_taken_or_hold_reason}")
```

**Total tool calls per tick: 2-3 (price + optional create + journal). NEVER exceed 5.**

## Wind-down

Stop all your open LONG executors: `manage_executors(action="stop", executor_id=...)`.
Do NOT close the SHORT or the user's LONG 520. Journal final combined P&L.

## Rules

- The user's LONG 520 is NOT your executor. Don't manage or close it.
- Do NOT call ToolSearch, manage_executors(executor_type=...) for schema, or any exploratory calls. You have everything you need above.
- Do NOT spend ticks "testing the MCP gateway". Just place the order or don't.
- If a tool call times out or errors: journal it and move on. Do not retry more than once.
- Keep ticks FAST: price → math → maybe create → journal → done.
