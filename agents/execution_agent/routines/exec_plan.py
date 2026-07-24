"""Executor payload builder — deterministic sizing + correct envelope shape.

Takes a synthesis signal dict and portfolio state, computes position size via
the risk formula, and returns the exact `manage_executors(action="create", ...)`
payload with properly typed int side/open_order_type.  The agent calls this
routine then passes the output dict directly to the MCP tool — no LLM
guesswork on envelope shape or type coercion.
"""

import logging
import math
from typing import Any

from pydantic import BaseModel, Field

from routines.base import RoutineResult

CATEGORY = "Execution Agent"

logger = logging.getLogger(__name__)


class Config(BaseModel):
    """Build a ready-to-submit executor payload from a synthesis signal."""

    connector_name: str = Field(
        default="gate_io_perpetual",
        description="Perpetual connector",
    )
    trading_pair: str = Field(
        description="Pair to trade, e.g. SOL-USDT",
    )
    direction: str = Field(
        description="'long' or 'short'",
    )
    entry_price: float = Field(
        description="Entry price from the signal",
    )
    confidence: float = Field(
        default=0.70,
        description="Synthesis composite confidence (0.0-1.0)",
    )
    take_profit_pct: float = Field(
        default=0.02,
        description="Take-profit as decimal, e.g. 0.02 = 2%",
    )
    stop_loss_pct: float = Field(
        default=0.015,
        description="Stop-loss as decimal, e.g. 0.015 = 1.5%",
    )
    time_limit_seconds: int = Field(
        default=3600,
        description="Time limit for the position in seconds",
    )
    trailing_stop_pct: float = Field(
        default=0.0,
        description="Trailing stop activation (0 = disabled)",
    )
    portfolio_balance_usdt: float = Field(
        description="Live portfolio balance in USDT (from get_portfolio_overview)",
    )
    current_drawdown_pct: float = Field(
        default=0.0,
        description="Current drawdown % (positive number, e.g. 3.5 = 3.5% down)",
    )
    risk_per_trade_pct: float = Field(
        default=3.0,
        description="Max loss per trade as % of portfolio (ceiling, not target)",
    )
    leverage: int = Field(default=20, description="Target leverage")
    max_position_size_quote: float = Field(
        default=50.0,
        description="Max notional per position in USDT",
    )
    controller_id: str = Field(
        default="main",
        description="Controller ID for the executor",
    )


async def run(config: Config, context: Any) -> RoutineResult:
    # --- 1. Determine side (int enum) ---
    direction = config.direction.strip().lower()
    if direction in ("long", "buy", "1"):
        side = 1
    elif direction in ("short", "sell", "2"):
        side = 2
    else:
        return RoutineResult(
            text=f"❌ Invalid direction '{config.direction}'. Must be 'long' or 'short'."
        )

    # --- 2. Risk-based sizing ---
    balance = config.portfolio_balance_usdt
    if balance <= 0:
        return RoutineResult(text="❌ Portfolio balance is zero or negative.")

    sl_distance = config.stop_loss_pct if config.stop_loss_pct > 0 else 0.015
    max_risk = balance * (config.risk_per_trade_pct / 100.0)
    raw_size_quote = max_risk / sl_distance

    # Confidence multiplier (only reduces)
    conf = config.confidence
    if conf < 0.60:
        return RoutineResult(text=f"❌ Confidence {conf:.2f} below 0.60 floor — refusing.")
    elif conf < 0.70:
        conf_mult = 0.5
    elif conf < 0.80:
        conf_mult = 0.75
    else:
        conf_mult = 1.0

    adjusted_size = raw_size_quote * conf_mult

    # Drawdown adjustment
    dd = config.current_drawdown_pct
    if dd > 4.0:
        adjusted_size *= 0.25
    elif dd > 2.0:
        adjusted_size *= 0.5

    # Cap at max_position_size_quote
    final_size_quote = min(adjusted_size, config.max_position_size_quote)

    # Verify implied loss never exceeds max_risk
    implied_loss = final_size_quote * sl_distance
    if implied_loss > max_risk:
        final_size_quote = max_risk / sl_distance

    # Convert to base amount
    if config.entry_price <= 0:
        return RoutineResult(text="❌ Entry price must be positive.")

    amount_base = final_size_quote / config.entry_price

    # --- 3. Build triple barrier config ---
    triple_barrier = {
        "take_profit": config.take_profit_pct,
        "stop_loss": config.stop_loss_pct,
        "time_limit": config.time_limit_seconds,
    }
    if config.trailing_stop_pct > 0:
        triple_barrier["trailing_stop"] = {
            "activation_price": config.trailing_stop_pct,
            "trailing_delta": config.trailing_stop_pct * 0.5,
        }

    # High confidence → market entry, medium → limit
    open_order_type = 1 if conf >= 0.80 else 2  # 1=MARKET, 2=LIMIT

    # --- 4. Build the executor payload ---
    executor_config = {
        "connector_name": config.connector_name,
        "trading_pair": config.trading_pair,
        "side": side,
        "amount": round(amount_base, 8),
        "leverage": config.leverage,
        "triple_barrier_config": triple_barrier,
        "open_order_type": open_order_type,
    }

    payload = {
        "action": "create",
        "executor_type": "position_executor",
        "controller_id": config.controller_id,
        "executor_config": executor_config,
    }

    # --- 5. Summary ---
    side_label = "LONG" if side == 1 else "SHORT"
    order_label = "MARKET" if open_order_type == 1 else "LIMIT"
    notional = round(final_size_quote, 2)

    lines = [
        f"**Exec Plan: {side_label} {config.trading_pair}**",
        f"- Entry: {config.entry_price:.6g} ({order_label})",
        f"- Size: {amount_base:.8f} base = ${notional} notional",
        f"- Leverage: {config.leverage}x",
        f"- TP: {config.take_profit_pct*100:.1f}%  SL: {config.stop_loss_pct*100:.1f}%",
        f"- Confidence: {conf:.2f} (mult {conf_mult}x)",
        f"- Drawdown: {dd:.1f}%",
        f"- Max risk: ${max_risk:.2f} → implied loss at SL: ${implied_loss:.2f}",
        "",
        "**Ready payload** (pass to `manage_executors`):",
        f"```json",
        _json_dump(payload),
        "```",
    ]

    return RoutineResult(
        text="\n".join(lines),
        table_data=[payload],
        sections=[{"title": "Payload", "content": payload}],
    )


def _json_dump(obj: Any) -> str:
    import json
    return json.dumps(obj, indent=2, default=str)
