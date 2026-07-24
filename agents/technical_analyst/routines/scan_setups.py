"""Batch candle fetch + indicator computation for all watchlist pairs.

Replaces N×MCP calls per technical tick with one deterministic routine call.
Returns an A/B/C setup table the LLM consumes in one read.
"""

import asyncio
import logging
import math
from typing import Any

from pydantic import BaseModel, Field

from routines.base import RoutineResult

CATEGORY = "Technical Analyst"
logger = logging.getLogger(__name__)


class Config(BaseModel):
    """Fetch candles + compute EMA/RSI/MACD/BB/ATR for watchlist pairs."""

    connector_name: str = Field(default="gate_io_perpetual")
    pairs: list[str] = Field(
        default=["BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "HYPE-USDT",
                 "INJ-USDT", "NEAR-USDT", "ORDI-USDT"],
        description="Pairs to analyse",
    )
    interval: str = Field(default="1h", description="Candle interval")
    limit: int = Field(default=60, description="Candles to fetch per pair")
    min_setup_grade: str = Field(default="B", description="Minimum grade to include (A+/A/B/C)")


async def _get_client():
    from hummingbot_api_client import HummingbotAPIClient
    from mcp_servers.hummingbot_api.settings import get_settings
    s = get_settings()
    c = HummingbotAPIClient(base_url=s.api_url, username=s.api_username,
                            password=s.api_password, timeout=s.client_timeout)
    await c.init()
    return c


def _ema(closes: list[float], period: int) -> list[float]:
    if len(closes) < period:
        return []
    k = 2 / (period + 1)
    result = [sum(closes[:period]) / period]
    for c in closes[period:]:
        result.append(c * k + result[-1] * (1 - k))
    return result


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    rs = ag / al
    return 100 - (100 / (1 + rs))


def _macd(closes: list[float]) -> tuple[float, float, float] | None:
    """Returns (macd_line, signal_line, histogram)."""
    e12 = _ema(closes, 12)
    e26 = _ema(closes, 26)
    if not e12 or not e26:
        return None
    min_len = min(len(e12), len(e26))
    macd_line = [e12[-(min_len - i)] - e26[-(min_len - i)] for i in range(min_len)]
    signal = _ema(macd_line, 9)
    if not signal:
        return None
    hist = macd_line[-1] - signal[-1]
    return macd_line[-1], signal[-1], hist


def _bb(closes: list[float], period: int = 20, std_mult: float = 2.0) -> tuple[float, float, float] | None:
    """Returns (upper, mid, lower)."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = sum(window) / period
    variance = sum((x - mid) ** 2 for x in window) / period
    std = math.sqrt(variance)
    return mid + std_mult * std, mid, mid - std_mult * std


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    return sum(trs[-period:]) / period


def _grade_setup(rsi: float | None, macd_hist: float | None, bb_pct: float | None,
                 ema_trend: str, atr_pct: float | None) -> str:
    """Grade A+/A/B/C based on indicator confluence."""
    score = 0
    if rsi is not None:
        if rsi < 35 or rsi > 65:
            score += 2
        elif rsi < 45 or rsi > 55:
            score += 1
    if macd_hist is not None and abs(macd_hist) > 0:
        score += 1
        if macd_hist > 0 and rsi and rsi < 60:
            score += 1
        elif macd_hist < 0 and rsi and rsi > 40:
            score += 1
    if bb_pct is not None:
        if bb_pct < 0.1 or bb_pct > 0.9:
            score += 2
        elif bb_pct < 0.2 or bb_pct > 0.8:
            score += 1
    if ema_trend in ("bullish", "bearish"):
        score += 1
    if atr_pct and atr_pct > 0.5:
        score += 1

    if score >= 7:
        return "A+"
    elif score >= 5:
        return "A"
    elif score >= 3:
        return "B"
    return "C"


async def _analyse_pair(client, connector: str, pair: str, interval: str, limit: int) -> dict:
    try:
        resp = await client.market_data.get_candles(connector, pair, interval=interval, max_records=limit)
        candles = resp if isinstance(resp, list) else (resp.get("candles") or resp.get("data") or [])
        if len(candles) < 30:
            return {"pair": pair, "grade": "C", "error": f"only {len(candles)} candles"}

        def _f(c, key, idx):
            if isinstance(c, dict):
                return float(c[key]) if key in c else float(c[idx])
            return float(c[idx])

        closes = [_f(c, "close", 4) for c in candles]
        highs  = [_f(c, "high",  2) for c in candles]
        lows   = [_f(c, "low",   3) for c in candles]
        price  = closes[-1]

        ema20 = _ema(closes, 20)
        ema50 = _ema(closes, 50)
        ema_trend = "neutral"
        if ema20 and ema50:
            if ema20[-1] > ema50[-1]:
                ema_trend = "bullish"
            elif ema20[-1] < ema50[-1]:
                ema_trend = "bearish"

        rsi_val = _rsi(closes)
        macd_result = _macd(closes)
        macd_hist = macd_result[2] if macd_result else None
        macd_line = macd_result[0] if macd_result else None
        bb_result = _bb(closes)
        bb_pct = None
        if bb_result:
            upper, mid, lower = bb_result
            bb_range = upper - lower
            bb_pct = (price - lower) / bb_range if bb_range > 0 else 0.5
        atr_val = _atr(highs, lows, closes)
        atr_pct = (atr_val / price * 100) if atr_val and price > 0 else None

        grade = _grade_setup(rsi_val, macd_hist, bb_pct, ema_trend, atr_pct)

        direction = "neutral"
        if ema_trend == "bullish" and rsi_val and rsi_val < 60 and macd_hist and macd_hist > 0:
            direction = "long"
        elif ema_trend == "bearish" and rsi_val and rsi_val > 40 and macd_hist and macd_hist < 0:
            direction = "short"

        return {
            "pair": pair,
            "price": round(price, 6),
            "grade": grade,
            "direction": direction,
            "ema_trend": ema_trend,
            "rsi": round(rsi_val, 1) if rsi_val is not None else None,
            "macd_hist": round(macd_hist, 6) if macd_hist is not None else None,
            "bb_pct": round(bb_pct, 3) if bb_pct is not None else None,
            "atr_pct": round(atr_pct, 3) if atr_pct is not None else None,
        }
    except Exception as e:
        return {"pair": pair, "grade": "C", "error": str(e)[:80]}


async def run(config: Config, context: Any) -> RoutineResult:
    client = await _get_client()
    try:
        tasks = [_analyse_pair(client, config.connector_name, p, config.interval, config.limit)
                 for p in config.pairs]
        results = await asyncio.gather(*tasks)
    finally:
        await client.close()

    grade_order = {"A+": 0, "A": 1, "B": 2, "C": 3}
    min_grade = config.min_setup_grade
    filtered = [r for r in results if grade_order.get(r["grade"], 3) <= grade_order.get(min_grade, 2)]
    filtered.sort(key=lambda x: grade_order.get(x["grade"], 3))

    columns = ["pair", "price", "grade", "direction", "ema_trend", "rsi", "macd_hist", "bb_pct", "atr_pct"]

    lines = [f"**Technical Setups** — {config.connector_name} {config.interval} — "
             f"{len(filtered)}/{len(results)} pairs ≥ {min_grade}"]
    lines.append("")
    lines.append("| Pair | Price | Grade | Dir | EMA | RSI | MACD hist | BB% | ATR% |")
    lines.append("|------|-------|-------|-----|-----|-----|-----------|-----|------|")
    for r in filtered:
        err = r.get("error", "")
        if err:
            lines.append(f"| {r['pair']} | — | {r['grade']} | — | — | — | — | — | ⚠️ {err[:40]} |")
        else:
            lines.append(
                f"| {r['pair']} | {r['price']:.6g} | {r['grade']} | {r['direction']} | "
                f"{r['ema_trend']} | {r['rsi']} | {r['macd_hist']} | {r['bb_pct']} | {r['atr_pct']} |"
            )

    return RoutineResult(text="\n".join(lines), table_data=filtered, table_columns=columns)
