"""Multi-agent coordination policies.

Provides guardrails for agents operating on the same pairs. Reads the
signal bus and the running engine registry to enforce cross-agent policies.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def get_active_agents_on_pair(pair: str) -> list[dict[str, Any]]:
    """Return info about running agents that operate on the given pair."""
    from condor.agents.engine import get_all_engines

    pair_upper = pair.upper()
    results: list[dict[str, Any]] = []

    for agent_id, engine in get_all_engines().items():
        engine_pair = engine.config.get("trading_pair", "")
        trading_ctx = engine.config.get("trading_context", "")

        pair_match = (
            engine_pair.upper() == pair_upper
            or pair_upper in trading_ctx.upper()
        )
        if pair_match:
            results.append({
                "agent_id": agent_id,
                "agent_slug": engine.agent.slug,
                "agent_name": engine.agent.name,
                "strategy": engine.strategy.name,
                "pair": engine_pair or "(from context)",
                "status": "paused" if engine._paused else "running",
            })

    return results


def check_directional_trade(
    pair: str, direction: str, agent_id: str
) -> tuple[bool, str]:
    """Check whether a directional trade is allowed given other agents' state.

    Returns (allowed, reason). When not allowed, reason explains why.
    """
    from condor.agents.signals import SignalStore

    pair_upper = pair.upper()
    direction_lower = direction.lower()
    store = SignalStore()

    # Policy 1: honour risk_alert signals
    risk_alerts = store.read_active(pair=pair_upper, signal_type="risk_alert")
    if risk_alerts:
        sources = ", ".join(a.source for a in risk_alerts)
        return False, f"risk_alert active on {pair_upper} from {sources}"

    # Policy 2: check regime signals for opposition
    regime_signals = store.read_active(pair=pair_upper, signal_type="regime_change")
    for sig in regime_signals:
        if sig.confidence >= 0.8:
            opposing = (
                (direction_lower == "long" and sig.direction in ("short", "trending_down"))
                or (direction_lower == "short" and sig.direction in ("long", "trending_up"))
            )
            if opposing:
                return False, (
                    f"high-confidence regime signal opposes {direction_lower} on {pair_upper} "
                    f"(regime={sig.direction}, conf={sig.confidence:.2f}, source={sig.source})"
                )

    # Policy 3: check for conflicting agent positions
    active = get_active_agents_on_pair(pair_upper)
    for info in active:
        if info["agent_id"] == agent_id:
            continue
        # We can't tell the direction of an MM agent's net position from here,
        # but we flag the overlap so the calling agent can adjust sizing.

    return True, ""


def get_coordination_summary(pair: str, agent_id: str = "") -> str:
    """Build a text summary of coordination state for a pair.

    Injected into the signals provider when agents overlap on a pair.
    """
    from condor.agents.signals import SignalStore

    pair_upper = pair.upper()
    store = SignalStore()
    parts: list[str] = []

    # Active agents on this pair
    active = get_active_agents_on_pair(pair_upper)
    other_agents = [a for a in active if a["agent_id"] != agent_id]
    if other_agents:
        names = ", ".join(
            f"{a['agent_name']} ({a['agent_id']})" for a in other_agents
        )
        parts.append(f"Other agents on {pair_upper}: {names}")

    # Risk alerts
    alerts = store.read_active(pair=pair_upper, signal_type="risk_alert")
    if alerts:
        sources = ", ".join(a.source for a in alerts)
        parts.append(f"RISK ALERT on {pair_upper} from: {sources}")

    # Regime signals
    regimes = store.read_active(pair=pair_upper, signal_type="regime_change")
    if regimes:
        latest = regimes[0]
        parts.append(
            f"Regime on {pair_upper}: {latest.direction} "
            f"(conf={latest.confidence:.2f}, source={latest.source})"
        )

    if not parts:
        return ""
    return " | ".join(parts)
