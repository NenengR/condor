### Position Executor
Takes directional positions with defined entry, stop-loss, and take-profit levels.

**Use when:**
- You have a clear directional view (bullish/bearish)
- You want automated stop-loss and take-profit management
- You want to define risk/reward ratios upfront

**Avoid when:**
- You want to provide liquidity (use Market Making instead)
- You need complex multi-leg strategies

**⚠ HEDGE MODE WARNING (Gate.io, Binance, and any dual-position exchange):**
In HEDGE mode (dual-position mode), the exchange tracks LONG and SHORT positions
separately. When `position_executor` fires its auto-TP or auto-SL exit, it sends a
plain SELL order **without** `position_action: 'CLOSE'`. The exchange interprets this
as opening a new SHORT, not closing the existing LONG — so your LONG margin never
reduces and SHORT margin accumulates.

**Affected connectors:** `gate_io_perpetual`, `binance_perpetual` (when hedge mode is
enabled), and any other connector using dual-position mode.

**Fix:** Do NOT rely on `triple_barrier_config` auto-close for LONG rungs on HEDGE-mode
connectors. Instead:
1. Open the rung with `position_executor` (entry only, no TP/SL in triple_barrier, or
   set `time_limit` only as a safety net).
2. Monitor price each tick. When TP/SL condition is met, close manually:
   ```
   manage_executors(action="stop", executor_id="...", keep_position=true)
   manage_executors(action="create", executor_type="order_executor",
     executor_config={"connector_name": "{connector}",
                      "trading_pair": "{pair}",
                      "side": 2,
                      "amount": {qty},
                      "execution_strategy": "MARKET",
                      "position_action": "CLOSE",
                      "leverage": {leverage}})
   ```
   `position_action: "CLOSE"` tells the exchange to reduce the LONG, not open a SHORT.

#### How It Works

- Opens a directional position (long/short) with optional limit entry price
- Manages exit via triple barrier config: stop-loss, take-profit, time limit, trailing stop
- Amount is in **base currency** (NOT quote). e.g., for BTC-USDT, amount=0.01 means 0.01 BTC

**CRITICAL:**
- `amount` is in **base currency** — NOT `total_amount_quote`. To convert from USD: `amount = usd_value / entry_price`
- Always fetch the schema first via progressive disclosure (`manage_executors(executor_type='position_executor')`) before creating

#### Parameter Reference

**Core:**
- `connector_name`: Exchange connector (e.g., 'binance_perpetual')
- `trading_pair`: Trading pair (e.g., 'BTC-USDT')
- `side`: 1 (BUY/LONG) or 2 (SELL/SHORT)
- `amount`: Position size in **base currency** (e.g., 0.01 BTC). To convert from USD: `amount = usd / price`
- `entry_price`: Limit entry price (optional — omit for market entry)
- `leverage`: Leverage multiplier (default: 1)

**Triple Barrier Config (`triple_barrier_config`):**
- `stop_loss`: Stop-loss as decimal (e.g., 0.02 = 2%)
- `take_profit`: Take-profit as decimal (e.g., 0.03 = 3%)
- `time_limit`: Max position duration in seconds (optional)
- `trailing_stop.activation_price`: Price delta to activate trailing stop
- `trailing_stop.trailing_delta`: Trailing distance
- `open_order_type`: 1=MARKET, 2=LIMIT, 3=LIMIT_MAKER
- `take_profit_order_type`: same enum (default: MARKET)
- `stop_loss_order_type`: same enum (default: MARKET)
- `time_limit_order_type`: same enum (default: MARKET)

**Optional:**
- `activation_bounds`: Price bounds for activation (optional)
- `level_id`: Optional identifier tag

#### Example

Long 0.01 BTC with 2% SL and 3% TP:
```json
{
  "connector_name": "binance_perpetual",
  "trading_pair": "BTC-USDT",
  "side": 1,
  "amount": 0.01,
  "leverage": 5,
  "triple_barrier_config": {
    "stop_loss": 0.02,
    "take_profit": 0.03,
    "open_order_type": 2
  }
}
```
