"""Deterministic confluence engine — reads active signal bus, applies weights +
partial-data penalties, and returns the next synthesis decision.

Replaces the LLM re-deriving this arithmetic from string-YAML each tick.
"""

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from routines.base import RoutineResult

CATEGORY = "Synthesis Strategist"
logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SIGNALS_DIR = _PROJECT_ROOT / "agents" / "_signals"


class Config(BaseModel):
    """Read active signals, compute confluence scores, return synthesis decisions."""

    min_publish_confidence: float = Field(
        default=0.30,
        description="Minimum composite |score| to publish a synthesis signal",
    )
    execution_threshold: float = Field(
        default=0.60,
        description="Composite confidence floor for execution_agent to trade",
    )
    weights: dict[str, float] = Field(
        default={"technical": 0.40, "sentiment": 0.30, "fundamental": 0.30},
        description="Source weights for confluence",
    )
    partial_data_penalty: float = Field(
        default=0.15,
        description="Confidence penalty per missing analyst source",
    )


def _load_active_signals() -> list[dict]:
    """Load all non-expired signals from the active/ directory."""
    active_dir = _SIGNALS_DIR / "active"
    signals = []
    if not active_dir.exists():
        return signals
    for f in active_dir.glob("*.json"):
        try:
            signals.append(json.loads(f.read_text()))
        except Exception:
            pass
    return signals


def _load_bus_recent(n: int = 50) -> list[dict]:
    """Load last N lines from bus.jsonl as fallback."""
    bus = _SIGNALS_DIR / "bus.jsonl"
    if not bus.exists():
        return []
    lines = bus.read_text().splitlines()[-n:]
    result = []
    for line in lines:
        try:
            result.append(json.loads(line))
        except Exception:
            pass
    return result


def _source_category(source: str) -> str:
    if "technical" in source:
        return "technical"
    if "sentiment" in source:
        return "sentiment"
    if "fundamental" in source:
        return "fundamental"
    return "other"


async def run(config: Config, context: Any) -> RoutineResult:
    import time
    now = time.time()

    # Load active signals; fall back to recent bus entries
    signals = _load_active_signals()
    if not signals:
        signals = _load_bus_recent(60)

    # Filter: analyst sources only, not synthesis, not expired
    analyst_signals = []
    for s in signals:
        src = s.get("source", s.get("metadata", {}).get("source", ""))
        if "synthesis" in src or "market_screener" in src:
            continue
        # Expiry check
        expires_at = s.get("expires_at")
        if expires_at:
            try:
                import datetime
                exp = datetime.datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if exp.timestamp() < now:
                    continue
            except Exception:
                pass
        analyst_signals.append(s)

    if not analyst_signals:
        return RoutineResult(
            text="**Confluence**: 0 active analyst signals — nothing to synthesize.",
            table_data=[],
        )

    # Group by pair
    from collections import defaultdict
    by_pair: dict[str, list[dict]] = defaultdict(list)
    for s in analyst_signals:
        pair = s.get("pair") or s.get("trading_pair") or "UNKNOWN"
        by_pair[pair].append(s)

    decisions = []
    lines = ["**Confluence Engine**", ""]
    lines.append("| Pair | Composite | Direction | Sources | Gaps | Action |")
    lines.append("|------|-----------|-----------|---------|------|--------|")

    for pair, sigs in sorted(by_pair.items()):
        # Collect per-category best signal
        cat_scores: dict[str, float] = {}
        cat_directions: dict[str, str] = {}
        for s in sigs:
            src = s.get("source", s.get("metadata", {}).get("source", ""))
            cat = _source_category(src)
            if cat == "other":
                continue
            conf = float(s.get("confidence", 0))
            direction = s.get("direction", "neutral")
            # Signed confidence: positive=long, negative=short, 0=neutral
            signed = conf if direction == "long" else (-conf if direction == "short" else 0)
            # Keep highest |confidence| per category
            if cat not in cat_scores or abs(signed) > abs(cat_scores[cat]):
                cat_scores[cat] = signed
                cat_directions[cat] = direction

        weights = config.weights
        present_sources = set(cat_scores.keys())
        all_sources = set(weights.keys())
        missing = all_sources - present_sources

        # Weighted composite
        composite = sum(cat_scores.get(cat, 0) * w for cat, w in weights.items())

        # Partial-data penalty
        penalty = len(missing) * config.partial_data_penalty
        composite_penalized = composite * (1 - penalty) if composite != 0 else 0

        abs_comp = abs(composite_penalized)
        direction = "long" if composite_penalized > 0 else ("short" if composite_penalized < 0 else "neutral")

        # Conflict detection
        directions_present = list(cat_directions.values())
        has_conflict = (
            "long" in directions_present and "short" in directions_present
        )

        if has_conflict:
            action = "SKIP (conflict)"
        elif abs_comp < config.min_publish_confidence:
            action = f"SKIP (score {abs_comp:.2f} < {config.min_publish_confidence})"
        elif abs_comp >= config.execution_threshold:
            action = f"PUBLISH → EXECUTE (conf {abs_comp:.2f} ≥ {config.execution_threshold})"
        else:
            action = f"PUBLISH (conf {abs_comp:.2f}, below exec threshold)"

        sources_str = ",".join(sorted(present_sources)) or "none"
        gaps_str = ",".join(sorted(missing)) or "—"

        lines.append(
            f"| {pair} | {composite_penalized:+.3f} | {direction} | "
            f"{sources_str} | {gaps_str} | {action} |"
        )

        decisions.append({
            "pair": pair,
            "composite": round(composite_penalized, 4),
            "direction": direction,
            "confidence": round(abs_comp, 4),
            "sources_present": sorted(present_sources),
            "sources_missing": sorted(missing),
            "has_conflict": has_conflict,
            "action": action,
            "publishable": not has_conflict and abs_comp >= config.min_publish_confidence,
            "executable": not has_conflict and abs_comp >= config.execution_threshold,
        })

    publishable = [d for d in decisions if d["publishable"]]
    executable = [d for d in decisions if d["executable"]]
    lines.append("")
    lines.append(f"**{len(publishable)} publishable**, **{len(executable)} executable** "
                 f"(threshold {config.execution_threshold})")

    return RoutineResult(
        text="\n".join(lines),
        table_data=decisions,
        table_columns=["pair", "composite", "direction", "confidence",
                        "sources_present", "sources_missing", "has_conflict", "action"],
    )
