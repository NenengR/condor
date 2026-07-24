"""Signal bus tool — thin MCP wrapper over condor.agents.signals.SignalStore.

Lets agents publish, read, and acknowledge trading signals. The source is
auto-derived from settings so agents cannot impersonate each other.
"""

from mcp_servers.condor.settings import settings


def _source() -> str:
    return settings.agent_slug or "chat"


async def manage_signal(
    action: str,
    signal_type: str = "",
    direction: str = "",
    pair: str = "",
    connector: str = "",
    confidence: float = 0.5,
    entry_price: float | None = None,
    take_profit: float | None = None,
    stop_loss: float | None = None,
    metadata: dict | None = None,
    expires_sec: int = 300,
    signal_id: str = "",
    agent_id: str = "",
    min_confidence: float = 0.0,
    limit: int = 20,
) -> dict:
    from condor.agents.signals import SignalStore

    store = SignalStore()

    if action == "publish":
        if not signal_type:
            return {"error": "signal_type is required (directional|regime_change|risk_alert|opportunity)"}
        if not direction:
            return {"error": "direction is required (long|short|neutral|reduce)"}
        if not pair:
            return {"error": "pair is required (e.g. SOL-USDT)"}

        sig = store.publish(
            source=_source(),
            signal_type=signal_type,
            direction=direction,
            pair=pair,
            connector=connector,
            confidence=confidence,
            entry_price=entry_price,
            take_profit=take_profit,
            stop_loss=stop_loss,
            metadata=metadata,
            expires_sec=expires_sec,
        )
        return {
            "published": True,
            "signal_id": sig.signal_id,
            "expires_at": sig.expires_at,
        }

    elif action == "read_active":
        signals = store.read_active(
            pair=pair or None,
            signal_type=signal_type or None,
            min_confidence=min_confidence,
        )
        return {
            "count": len(signals),
            "signals": [s.to_dict() for s in signals],
        }

    elif action == "read_recent":
        signals = store.read_recent(limit=limit)
        return {
            "count": len(signals),
            "signals": [s.to_dict() for s in signals],
        }

    elif action == "acknowledge":
        if not signal_id:
            return {"error": "signal_id is required for acknowledge"}
        ack_agent = agent_id or _source()
        ok = store.acknowledge(signal_id, ack_agent)
        if not ok:
            return {"error": f"Signal '{signal_id}' not found or already expired"}
        return {"acknowledged": True, "signal_id": signal_id, "by": ack_agent}

    else:
        return {"error": f"Unknown action '{action}'. Use: publish, read_active, read_recent, acknowledge"}
