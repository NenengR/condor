"""Pipeline health & signals API — surfaces signal bus data and per-agent
tick health for the frontend dashboard."""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline", tags=["pipeline"])

_AGENTS_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "agents"
_SIGNALS_ROOT = _AGENTS_ROOT / "_signals"
_BUS_PATH = _SIGNALS_ROOT / "bus.jsonl"
_ACTIVE_DIR = _SIGNALS_ROOT / "active"

_KNOWN_AGENTS = [
    "market_screener",
    "technical_analyst",
    "sentiment_analyst",
    "fundamental_analyst",
    "synthesis_strategist",
    "execution_agent",
]


def _read_bus_tail(limit: int) -> list[dict[str, Any]]:
    if not _BUS_PATH.exists():
        return []
    try:
        with _BUS_PATH.open() as f:
            lines = deque(f, maxlen=limit)
        result = []
        for line in reversed(lines):
            line = line.strip()
            if line:
                try:
                    result.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return result
    except Exception:
        logger.exception("Failed to read bus.jsonl")
        return []


def _read_active_signals() -> list[dict[str, Any]]:
    if not _ACTIVE_DIR.exists():
        return []
    signals = []
    now = datetime.now(timezone.utc)
    for p in sorted(_ACTIVE_DIR.glob("*.yml"), reverse=True):
        try:
            data = yaml.safe_load(p.read_text())
            if not data:
                continue
            exp = data.get("expires_at", "")
            if exp:
                try:
                    if datetime.fromisoformat(exp) < now:
                        continue
                except (ValueError, TypeError):
                    pass
            signals.append(data)
        except Exception:
            pass
    return signals


def _find_latest_session(strategy_dir: Path) -> Path | None:
    sessions_dir = strategy_dir / "sessions"
    if not sessions_dir.exists():
        return None
    best_num = -1
    best_dir = None
    for d in sessions_dir.iterdir():
        if not d.is_dir() or not d.name.startswith("session_"):
            continue
        try:
            num = int(d.name.split("_", 1)[1])
            if num > best_num:
                best_num = num
                best_dir = d
        except (ValueError, IndexError):
            continue
    return best_dir


def _read_agent_health() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    agents_health: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    for agent_id in _KNOWN_AGENTS:
        agent_dir = _AGENTS_ROOT / agent_id
        if not agent_dir.is_dir():
            continue

        strategies_dir = agent_dir / "strategies"
        if not strategies_dir.is_dir():
            agents_health.append({
                "agent_id": agent_id,
                "strategy": None,
                "status": "stopped",
                "last_tick": None,
                "session_num": None,
                "last_tick_at": None,
            })
            alerts.append({
                "level": "warning",
                "agent_id": agent_id,
                "message": f"{agent_id}: no strategies found",
                "timestamp": now.isoformat(),
            })
            continue

        for strat_dir in sorted(strategies_dir.iterdir()):
            if not strat_dir.is_dir() or strat_dir.name.startswith("_"):
                continue

            session_dir = _find_latest_session(strat_dir)
            if not session_dir:
                agents_health.append({
                    "agent_id": agent_id,
                    "strategy": strat_dir.name,
                    "status": "stopped",
                    "last_tick": None,
                    "session_num": None,
                    "last_tick_at": None,
                })
                alerts.append({
                    "level": "warning",
                    "agent_id": agent_id,
                    "message": f"{agent_id}/{strat_dir.name}: no active session",
                    "timestamp": now.isoformat(),
                })
                continue

            meta_path = session_dir / "meta.yaml"
            session_num = int(session_dir.name.split("_", 1)[1])

            if not meta_path.exists():
                agents_health.append({
                    "agent_id": agent_id,
                    "strategy": strat_dir.name,
                    "status": "unknown",
                    "last_tick": None,
                    "session_num": session_num,
                    "last_tick_at": None,
                })
                continue

            try:
                meta = yaml.safe_load(meta_path.read_text()) or {}
            except Exception:
                meta = {}

            last_tick = meta.get("last_tick_health", {})
            mtime = datetime.fromtimestamp(
                meta_path.stat().st_mtime, tz=timezone.utc
            )

            degraded = last_tick.get("degraded", False)
            consecutive_empty = last_tick.get("consecutive_empty", 0)

            age_seconds = (now - mtime).total_seconds()

            if degraded:
                status = "degraded"
            elif age_seconds > 900:
                status = "stale"
            else:
                status = "healthy"

            agents_health.append({
                "agent_id": agent_id,
                "strategy": strat_dir.name,
                "status": status,
                "last_tick": last_tick,
                "session_num": session_num,
                "last_tick_at": mtime.isoformat(),
            })

            if degraded:
                alerts.append({
                    "level": "warning",
                    "agent_id": agent_id,
                    "message": f"{agent_id}: DEGRADED ({consecutive_empty} consecutive empty ticks)",
                    "timestamp": mtime.isoformat(),
                })
            if consecutive_empty >= 3:
                alerts.append({
                    "level": "error",
                    "agent_id": agent_id,
                    "message": f"{agent_id}: {consecutive_empty} consecutive empty ticks — possible API key or quota issue",
                    "timestamp": mtime.isoformat(),
                })
            if age_seconds > 900:
                alerts.append({
                    "level": "warning",
                    "agent_id": agent_id,
                    "message": f"{agent_id}/{strat_dir.name}: last tick was {int(age_seconds // 60)}m ago — may be stopped",
                    "timestamp": now.isoformat(),
                })

    return agents_health, alerts


@router.get("/signals")
async def get_signals(limit: int = Query(default=50, ge=1, le=500)):
    recent = _read_bus_tail(limit)
    active = _read_active_signals()
    return {"recent": recent, "active": active}


@router.get("/health")
async def get_health():
    agents, alerts = _read_agent_health()
    return {"agents": agents, "alerts": alerts}
