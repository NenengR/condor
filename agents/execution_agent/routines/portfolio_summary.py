"""Portfolio summary — net long/short inventory + per-pair direction map.

Provides the same-pair/opposite-side block (Step 3.5) with a clean struct
instead of the LLM parsing raw executor JSON each tick.
"""

import logging
from typing import Any

from pydantic import BaseModel, Field

from routines.base import RoutineResult

CATEGORY = "Execution Agent"
logger = logging.getLogger(__name__)


class Config(BaseModel):
    """Fetch open executors + positions, return per-pair direction inventory."""

    connector_name: str = Field(default="gate_io_perpetual")
    account_name: str = Field(default="master_account")


async def _get_client():
    from hummingbot_api_client import HummingbotAPIClient
    from mcp_servers.hummingbot_api.settings import get_settings
    s = get_settings()
    c = HummingbotAPIClient(base_url=s.api_url, username=s.api_username,
                            password=s.api_password, timeout=s.client_timeout)
    await c.init()
    return c


async def run(config: Config, context: Any) -> RoutineResult:
    client = await _get_client()
    try:
        # Fetch executors (running only)
        try:
            exec_resp = await client.executors.search_executors(
                account_names=[config.account_name],
                connector_names=[config.connector_name],
                status="running",
            )
            executors = exec_resp.get("data", exec_resp.get("executors", []))
            if isinstance(executors, dict):
                executors = executors.get("items", [])
        except Exception as e:
            executors = []
            logger.warning(f"Could not fetch executors: {e}")

        # Fetch positions
        try:
            pos_resp = await client.trading.get_open_positions(
                account_name=config.account_name,
                connector_name=config.connector_name,
            )
            positions = pos_resp if isinstance(pos_resp, list) else pos_resp.get("data", pos_resp.get("positions", []))
        except Exception as e:
            positions = []
            logger.warning(f"Could not fetch positions: {e}")
    finally:
        await client.close()

    # Build per-pair inventory
    from collections import defaultdict
    pair_inventory: dict[str, dict] = defaultdict(lambda: {
        "long_notional": 0.0, "short_notional": 0.0,
        "long_pnl": 0.0, "short_pnl": 0.0,
        "long_executors": 0, "short_executors": 0,
        "sides": set(),
    })

    for ex in executors:
        if not isinstance(ex, dict):
            continue
        status = ex.get("status", ex.get("executor_status", ""))
        if status not in ("RUNNING", "running", "active", 1, "ACTIVE"):
            continue
        cfg = ex.get("config", ex.get("executor_config", {}))
        pair = cfg.get("trading_pair") or ex.get("trading_pair", "")
        side_raw = cfg.get("side", ex.get("side", 0))
        side = int(side_raw) if str(side_raw).isdigit() else (1 if str(side_raw).upper() in ("BUY","LONG") else 2)
        notional = float(ex.get("net_pnl_quote", 0)) + float(cfg.get("amount", 0)) * float(cfg.get("entry_price", cfg.get("current_price", 1)))
        pnl = float(ex.get("net_pnl_quote", ex.get("unrealized_pnl", 0)))

        if not pair:
            continue
        inv = pair_inventory[pair]
        if side == 1:
            inv["long_notional"] += notional
            inv["long_pnl"] += pnl
            inv["long_executors"] += 1
            inv["sides"].add("long")
        else:
            inv["short_notional"] += notional
            inv["short_pnl"] += pnl
            inv["short_executors"] += 1
            inv["sides"].add("short")

    for pos in positions:
        if not isinstance(pos, dict):
            continue
        pair = pos.get("trading_pair", pos.get("symbol", ""))
        amount = float(pos.get("amount", pos.get("position_size", 0)))
        if amount == 0 or not pair:
            continue
        pnl = float(pos.get("unrealized_pnl", 0))
        price = float(pos.get("entry_price", pos.get("mark_price", 1)))
        notional = abs(amount) * price
        inv = pair_inventory[pair]
        if amount > 0:
            inv["long_notional"] = max(inv["long_notional"], notional)
            inv["long_pnl"] += pnl
            inv["sides"].add("long")
        else:
            inv["short_notional"] = max(inv["short_notional"], notional)
            inv["short_pnl"] += pnl
            inv["sides"].add("short")

    # Build summary rows
    rows = []
    total_long_pnl = 0.0
    total_short_pnl = 0.0
    for pair, inv in sorted(pair_inventory.items()):
        sides = sorted(inv["sides"])
        hedged = len(sides) > 1
        rows.append({
            "pair": pair,
            "sides": sides,
            "hedged": hedged,
            "long_notional": round(inv["long_notional"], 2),
            "short_notional": round(inv["short_notional"], 2),
            "long_pnl": round(inv["long_pnl"], 4),
            "short_pnl": round(inv["short_pnl"], 4),
            "long_executors": inv["long_executors"],
            "short_executors": inv["short_executors"],
        })
        total_long_pnl += inv["long_pnl"]
        total_short_pnl += inv["short_pnl"]

    # Direction-inventory map for Step 3.5 (same-pair/opposite-side block)
    direction_map = {}
    for r in rows:
        direction_map[r["pair"]] = {
            "sides": r["sides"],
            "hedged": r["hedged"],
            "long_notional": r["long_notional"],
            "short_notional": r["short_notional"],
        }

    lines = ["**Portfolio Summary**", ""]
    if not rows:
        lines.append("No open positions or executors.")
    else:
        lines.append("| Pair | Sides | Long $ | Short $ | Long PnL | Short PnL | ⚠️ |")
        lines.append("|------|-------|--------|---------|----------|-----------|-----|")
        for r in rows:
            warn = "HEDGED" if r["hedged"] else ""
            lines.append(
                f"| {r['pair']} | {','.join(r['sides'])} | "
                f"${r['long_notional']} | ${r['short_notional']} | "
                f"{r['long_pnl']:+.4f} | {r['short_pnl']:+.4f} | {warn} |"
            )
        lines.append("")
        lines.append(f"Net PnL — Long: {total_long_pnl:+.4f} USDT | Short: {total_short_pnl:+.4f} USDT | "
                     f"Combined: {total_long_pnl + total_short_pnl:+.4f} USDT")

    hedged_pairs = [r["pair"] for r in rows if r["hedged"]]
    if hedged_pairs:
        lines.append(f"\n⚠️ **Hedged pairs (BLOCK new orders):** {', '.join(hedged_pairs)}")

    return RoutineResult(
        text="\n".join(lines),
        table_data=rows,
        table_columns=["pair", "sides", "hedged", "long_notional", "short_notional",
                        "long_pnl", "short_pnl"],
        sections=[{"title": "direction_map", "content": direction_map}],
    )
