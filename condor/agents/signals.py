"""SignalStore -- file-based pub/sub for trading signals between agents.

Storage layout::

    agents/_signals/
        bus.jsonl            # append-only log of all signals (history)
        active/              # one YAML file per unexpired signal
            {signal_id}.yml
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

log = logging.getLogger(__name__)

_SIGNALS_ROOT = Path(__file__).parent.parent.parent / "agents" / "_signals"
_ACTIVE_DIR = _SIGNALS_ROOT / "active"
_BUS_PATH = _SIGNALS_ROOT / "bus.jsonl"

VALID_SIGNAL_TYPES = {"directional", "regime_change", "risk_alert", "opportunity"}
VALID_DIRECTIONS = {"long", "short", "neutral", "reduce"}


@dataclass
class Signal:
    signal_id: str
    source: str  # agent_id or external source name
    signal_type: str  # directional | regime_change | risk_alert | opportunity
    direction: str  # long | short | neutral | reduce
    pair: str  # e.g. "SOL-USDT"
    connector: str  # e.g. "binance_perpetual"
    confidence: float = 0.0  # 0.0 to 1.0
    entry_price: float | None = None
    take_profit: float | None = None
    stop_loss: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    expires_at: str = ""
    created_at: str = ""
    acknowledged_by: list[str] = field(default_factory=list)

    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        try:
            exp = datetime.fromisoformat(self.expires_at)
            return datetime.now(timezone.utc) >= exp
        except (ValueError, TypeError):
            return False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Signal:
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in valid_fields}
        return cls(**filtered)


class SignalStore:
    """Publish/subscribe store for inter-agent trading signals."""

    def __init__(self, root: Path | None = None):
        self._root = root or _SIGNALS_ROOT
        self._active_dir = self._root / "active"
        self._bus_path = self._root / "bus.jsonl"

    def _ensure_dirs(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        self._active_dir.mkdir(parents=True, exist_ok=True)

    def publish(
        self,
        source: str,
        signal_type: str,
        direction: str,
        pair: str,
        connector: str = "",
        confidence: float = 0.5,
        entry_price: float | None = None,
        take_profit: float | None = None,
        stop_loss: float | None = None,
        metadata: dict[str, Any] | None = None,
        expires_sec: int = 300,
    ) -> Signal:
        """Publish a new signal to the bus and active store."""
        self._ensure_dirs()

        now = datetime.now(timezone.utc)
        exp = datetime.fromtimestamp(
            now.timestamp() + expires_sec, tz=timezone.utc
        )

        sig = Signal(
            signal_id=uuid4().hex[:8],
            source=source,
            signal_type=signal_type,
            direction=direction,
            pair=pair,
            connector=connector,
            confidence=max(0.0, min(1.0, confidence)),
            entry_price=entry_price,
            take_profit=take_profit,
            stop_loss=stop_loss,
            metadata=metadata or {},
            expires_at=exp.isoformat(),
            created_at=now.isoformat(),
            acknowledged_by=[],
        )

        # Write to active directory
        path = self._active_dir / f"{sig.signal_id}.yml"
        path.write_text(yaml.dump(sig.to_dict(), default_flow_style=False))

        # Append to bus log
        try:
            with self._bus_path.open("a") as f:
                f.write(json.dumps(sig.to_dict()) + "\n")
        except Exception:
            log.exception("Failed to append to signal bus")

        log.info(
            "Signal published: %s %s %s %s conf=%.2f (source=%s, expires=%s)",
            sig.signal_id, sig.signal_type, sig.direction, sig.pair,
            sig.confidence, sig.source, sig.expires_at,
        )
        return sig

    def read_active(
        self,
        pair: str | None = None,
        signal_type: str | None = None,
        min_confidence: float = 0.0,
    ) -> list[Signal]:
        """Read active (unexpired) signals, optionally filtered."""
        if not self._active_dir.exists():
            return []

        signals: list[Signal] = []
        for path in sorted(self._active_dir.glob("*.yml")):
            try:
                data = yaml.safe_load(path.read_text())
                if not data:
                    continue
                sig = Signal.from_dict(data)
                if sig.is_expired():
                    continue
                if pair and sig.pair.upper() != pair.upper():
                    continue
                if signal_type and sig.signal_type != signal_type:
                    continue
                if sig.confidence < min_confidence:
                    continue
                signals.append(sig)
            except Exception:
                log.exception("Failed to read signal file %s", path)

        # Sort by confidence descending, then recency
        signals.sort(key=lambda s: (-s.confidence, s.created_at), reverse=False)
        return signals

    def read_recent(self, limit: int = 20) -> list[Signal]:
        """Read the most recent signals from the bus log."""
        if not self._bus_path.exists():
            return []

        try:
            lines = self._bus_path.read_text().strip().splitlines()
        except Exception:
            return []

        signals: list[Signal] = []
        for line in lines[-limit:]:
            try:
                data = json.loads(line)
                signals.append(Signal.from_dict(data))
            except Exception:
                continue
        return signals

    def expire(self) -> int:
        """Remove expired signals from the active directory. Returns count removed."""
        if not self._active_dir.exists():
            return 0

        removed = 0
        for path in list(self._active_dir.glob("*.yml")):
            try:
                data = yaml.safe_load(path.read_text())
                if not data:
                    path.unlink(missing_ok=True)
                    removed += 1
                    continue
                sig = Signal.from_dict(data)
                if sig.is_expired():
                    path.unlink(missing_ok=True)
                    removed += 1
            except Exception:
                log.exception("Failed to check expiry for %s", path)

        if removed:
            log.debug("Expired %d signals", removed)
        return removed

    def acknowledge(self, signal_id: str, agent_id: str) -> bool:
        """Mark a signal as acted upon by an agent."""
        path = self._active_dir / f"{signal_id}.yml"
        if not path.exists():
            return False

        try:
            data = yaml.safe_load(path.read_text())
            if not data:
                return False
            ack_list = data.get("acknowledged_by", [])
            if agent_id not in ack_list:
                ack_list.append(agent_id)
                data["acknowledged_by"] = ack_list
                path.write_text(yaml.dump(data, default_flow_style=False))
            return True
        except Exception:
            log.exception("Failed to acknowledge signal %s", signal_id)
            return False

    def get(self, signal_id: str) -> Signal | None:
        """Get a single signal by ID."""
        path = self._active_dir / f"{signal_id}.yml"
        if not path.exists():
            return None
        try:
            data = yaml.safe_load(path.read_text())
            return Signal.from_dict(data) if data else None
        except Exception:
            return None

    def rotate_bus(self, max_lines: int = 5000) -> None:
        """Trim the bus log to keep only the most recent entries."""
        if not self._bus_path.exists():
            return
        try:
            lines = self._bus_path.read_text().strip().splitlines()
            if len(lines) > max_lines:
                self._bus_path.write_text(
                    "\n".join(lines[-max_lines:]) + "\n"
                )
        except Exception:
            log.exception("Failed to rotate signal bus")
