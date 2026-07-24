"""Batch universe scanner — prices, funding, momentum in one deterministic call.

Replaces the 15-25 MCP tool calls per screener tick with a single routine
invocation that returns a ranked table ready for signal publishing.
"""

import asyncio
import logging
from typing import Any

from pydantic import BaseModel, Field

from routines.base import RoutineResult

CATEGORY = "Market Screener"

logger = logging.getLogger(__name__)


class Config(BaseModel):
    """Scan the perpetual universe: batch prices + funding + 24h momentum."""

    connector_name: str = Field(
        default="gate_io_perpetual",
        description="Perpetual connector to scan",
    )
    universe: list[str] = Field(
        default=[
            "BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT",
            "DOGE-USDT", "AVAX-USDT", "LINK-USDT", "AAVE-USDT", "ARB-USDT",
            "OP-USDT", "SUI-USDT", "APT-USDT", "TIA-USDT", "SEI-USDT",
            "WIF-USDT", "PEPE-USDT", "NEAR-USDT", "INJ-USDT", "HYPE-USDT",
            "ORDI-USDT", "LTC-USDT", "ADA-USDT", "DOT-USDT",
        ],
        description="Pairs to scan",
    )
    watchlist_size: int = Field(default=6, description="Top-N to return")
    funding_extreme_pct: float = Field(
        default=0.10,
        description="Funding rate % threshold for squeeze-risk flag",
    )


async def _get_client():
    """Create a fresh HummingbotAPIClient from persisted server config."""
    from hummingbot_api_client import HummingbotAPIClient
    from mcp_servers.hummingbot_api.settings import get_settings

    s = get_settings()
    client = HummingbotAPIClient(
        base_url=s.api_url,
        username=s.api_username,
        password=s.api_password,
        timeout=s.client_timeout,
    )
    await client.init()
    return client


async def run(config: Config, context: Any) -> RoutineResult:
    client = await _get_client()
    try:
        return await _scan(client, config)
    finally:
        await client.close()


async def _scan(client, config: Config) -> RoutineResult:
    connector = config.connector_name
    universe = config.universe

    # --- 1. Batch prices (with retry for 429) ---
    prices: dict = {}
    for attempt in range(3):
        try:
            prices_resp = await client.market_data.get_prices(connector, universe)
            if isinstance(prices_resp, dict):
                prices = prices_resp.get("prices", prices_resp)
            elif isinstance(prices_resp, list):
                # list of {pair, price} dicts
                prices = {p.get("trading_pair", p.get("pair", "")): p.get("price") for p in prices_resp}
            break
        except Exception as e:
            if "429" in str(e) and attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            logger.warning(f"Batch prices failed (attempt {attempt+1}): {e}")
            break

    if not prices:
        return RoutineResult(text="⚠️ Could not fetch prices — exchange may be rate-limiting.")

    # --- 2. Funding rates (parallel, with error tolerance) ---
    async def _fetch_funding(pair: str) -> tuple[str, float | None]:
        try:
            resp = await client.market_data.get_funding_info(connector, pair)
            rate = resp.get("rate") or resp.get("funding_rate")
            if rate is not None:
                return pair, float(rate) * 100  # to percent
        except Exception:
            pass
        return pair, None

    funding_tasks = [_fetch_funding(p) for p in universe]
    funding_results = await asyncio.gather(*funding_tasks)
    funding_map: dict[str, float | None] = dict(funding_results)

    # --- 3. 24h candles for momentum (top movers only to save calls) ---
    async def _fetch_momentum(pair: str) -> tuple[str, float, float]:
        """Returns (pair, pct_change_4h, avg_range_pct)."""
        try:
            resp = await client.market_data.get_candles(
                connector, pair, interval="1h", max_records=24
            )
            candles = resp if isinstance(resp, list) else (resp.get("candles") or resp.get("data") or [])
            if not candles or len(candles) < 4:
                return pair, 0.0, 0.0

            closes = [float(c["close"] if isinstance(c, dict) and "close" in c else c[4]) for c in candles]
            highs = [float(c["high"] if isinstance(c, dict) and "high" in c else c[2]) for c in candles]
            lows = [float(c["low"] if isinstance(c, dict) and "low" in c else c[3]) for c in candles]

            # 4h momentum: last 4 closes
            if closes[-1] and closes[-4]:
                pct_4h = ((closes[-1] - closes[-4]) / closes[-4]) * 100
            else:
                pct_4h = 0.0

            # Average hourly range as % of price
            ranges = []
            for h, l, c in zip(highs, lows, closes):
                if c > 0:
                    ranges.append(((h - l) / c) * 100)
            avg_range = sum(ranges) / len(ranges) if ranges else 0.0

            return pair, pct_4h, avg_range
        except Exception:
            return pair, 0.0, 0.0

    momentum_tasks = [_fetch_momentum(p) for p in universe]
    momentum_results = await asyncio.gather(*momentum_tasks)
    momentum_map = {p: (m, v) for p, m, v in momentum_results}

    # --- 4. Score each pair ---
    scored: list[dict] = []
    avoid: list[str] = []

    for pair in universe:
        price = prices.get(pair)
        if not price:
            continue

        funding_pct = funding_map.get(pair)
        momentum_4h, avg_range = momentum_map.get(pair, (0.0, 0.0))

        # Dead pair filter
        if avg_range < 0.05:
            continue

        abs_funding = abs(funding_pct) if funding_pct is not None else 0.0
        funding_score = min(1.0, abs_funding / config.funding_extreme_pct)
        momentum_score = min(1.0, abs(momentum_4h) / 5.0)
        volatility_score = min(1.0, avg_range / 2.0)

        composite = 0.40 * funding_score + 0.35 * momentum_score + 0.25 * volatility_score

        if abs_funding >= config.funding_extreme_pct:
            avoid.append(pair)

        scored.append({
            "pair": pair,
            "price": float(price) if price else 0,
            "funding_pct": round(funding_pct, 4) if funding_pct is not None else None,
            "momentum_4h": round(momentum_4h, 2),
            "avg_range_pct": round(avg_range, 3),
            "composite": round(composite, 4),
        })

    # --- 5. Rank and build watchlist ---
    scored.sort(key=lambda x: x["composite"], reverse=True)
    watchlist = scored[: config.watchlist_size]

    # --- 6. Build result ---
    columns = ["pair", "price", "funding_pct", "momentum_4h", "avg_range_pct", "composite"]

    lines = [f"**Universe Scan** — {connector} — {len(scored)} active pairs"]
    lines.append("")
    lines.append("| # | Pair | Price | Funding% | 4h Move% | Vol% | Score |")
    lines.append("|---|------|-------|----------|----------|------|-------|")
    for i, row in enumerate(watchlist, 1):
        flag = " ⚠️" if row["pair"] in avoid else ""
        lines.append(
            f"| {i} | {row['pair']}{flag} | {row['price']:.6g} | "
            f"{row['funding_pct']:.4f} | {row['momentum_4h']:+.2f} | "
            f"{row['avg_range_pct']:.3f} | {row['composite']:.4f} |"
        )

    if avoid:
        lines.append("")
        lines.append(f"⚠️ **Avoid (extreme funding):** {', '.join(avoid)}")

    return RoutineResult(
        text="\n".join(lines),
        table_data=scored,
        table_columns=columns,
    )
