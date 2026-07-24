"""Core data provider: active trading signals from the signal bus.

Runs every tick, surfaces active signals to the agent's prompt so it
can react without wasting a tool call on polling.
"""

from __future__ import annotations

from typing import Any

from . import register_provider
from .base import BaseProvider, ProviderResult


class SignalsProvider(BaseProvider):
    name = "signals"
    is_core = True

    async def execute(
        self, client: Any, config: dict, agent_id: str = ""
    ) -> ProviderResult:
        from condor.agents.signals import SignalStore

        store = SignalStore()

        # Expire stale signals each tick
        store.expire()

        # Read active signals -- optionally scoped to the agent's pair
        pair_filter = config.get("trading_pair")
        active = store.read_active(pair=pair_filter)

        if not active:
            return ProviderResult(
                name=self.name,
                data={"signals": [], "count": 0},
                summary="Active Signals: none",
            )

        lines = [f"Active Signals ({len(active)}):"]
        signal_dicts = []
        for sig in active:
            ack_note = ""
            if agent_id and agent_id in sig.acknowledged_by:
                ack_note = " [ACK]"
            lines.append(
                f"  [{sig.signal_id}] {sig.pair} {sig.direction.upper()} "
                f"conf={sig.confidence:.2f} type={sig.signal_type} "
                f"(source: {sig.source}){ack_note}"
            )
            signal_dicts.append(sig.to_dict())

        return ProviderResult(
            name=self.name,
            data={"signals": signal_dicts, "count": len(active)},
            summary="\n".join(lines),
        )


register_provider(SignalsProvider())
