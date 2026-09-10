"""What may reach paper capital, and what may not.

The engine is fail-closed on purpose.  A mechanism is eligible only if its own
``results/verdict.json`` says ``PAPER_ELIGIBLE`` **and** that verdict names a
forward seal.  Anything else, including a mechanism that looks excellent in
history, produces zero decisions.

So the expected output of this engine today is: no eligible mechanism, no paper
decision, a flat journal.  That is the correct behaviour, not a bug — five flat
paper portfolios were the honest answer here once before.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from research_kernel.cost_model import CostModel
from research_kernel.mechanism_spec import MechanismSpec
from research_kernel.verdict import PROMOTED_STATUSES, Verdict, VerdictStatus

MECHANISMS_ROOT = Path("mechanisms")


@dataclass
class RiskState:
    daily_pnl: float = 0.0
    drawdown: float = 0.0
    open_exposure: float = 0.0
    consecutive_losses: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "daily_pnl": self.daily_pnl,
            "drawdown": self.drawdown,
            "open_exposure": self.open_exposure,
            "consecutive_losses": self.consecutive_losses,
        }


@dataclass
class RiskLimits:
    max_daily_loss: float = 500.0
    max_drawdown: float = 2_000.0
    max_gross_exposure: float = 20_000.0
    max_consecutive_losses: int = 2

    def breach(self, state: RiskState, add_notional: float) -> Optional[str]:
        if state.daily_pnl <= -abs(self.max_daily_loss):
            return "daily loss limit reached"
        if state.drawdown >= abs(self.max_drawdown):
            return "drawdown limit reached"
        if state.open_exposure + add_notional > self.max_gross_exposure:
            return "gross exposure limit reached"
        if state.consecutive_losses >= self.max_consecutive_losses:
            return "two consecutive losses"
        return None


@dataclass
class EligibleMechanism:
    spec: MechanismSpec
    verdict: Verdict
    cost: CostModel


def load_eligible(root: Path = MECHANISMS_ROOT) -> List[EligibleMechanism]:
    """Read every mechanism directory and keep only the promoted ones."""
    out: List[EligibleMechanism] = []
    root = Path(root)
    if not root.exists():
        return out
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        spec_path, verdict_path = d / "spec.json", d / "results" / "verdict.json"
        if not (spec_path.exists() and verdict_path.exists()):
            continue
        with verdict_path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        try:
            verdict = Verdict.from_dict(payload)
        except Exception:
            continue
        if verdict.status not in PROMOTED_STATUSES:
            continue
        spec = MechanismSpec.from_json(spec_path)
        if verdict.rules_hash and verdict.rules_hash != spec.rules_hash():
            continue  # the rule moved after the verdict was written
        out.append(EligibleMechanism(spec=spec, verdict=verdict, cost=spec.cost()))
    return out


@dataclass
class Signal:
    mechanism_id: str
    exchange: str
    symbol: str
    side: str
    gross_edge_bps_est: float
    data_latency_ms: float
    notional: float
    timestamp: str = ""


@dataclass
class DecisionEngine:
    eligible: List[EligibleMechanism] = field(default_factory=list)
    limits: RiskLimits = field(default_factory=RiskLimits)
    cost_multiple_required: float = 2.0

    def decide(self, signal: Signal, risk: RiskState) -> Dict[str, Any]:
        """One signal in, one journal-shaped record out."""
        now = signal.timestamp or datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        match = next(
            (e for e in self.eligible if e.spec.mechanism_id == signal.mechanism_id),
            None,
        )
        base = {
            "timestamp": now,
            "mechanism_id": signal.mechanism_id,
            "exchange": signal.exchange,
            "symbol": signal.symbol,
            "side": signal.side,
            "gross_edge_bps_est": float(signal.gross_edge_bps_est),
            "cost_bps_est": 0.0,
            "net_edge_bps_est": 0.0,
            "position_notional_paper": 0.0,
            "decision": "REJECT",
            "reject_reason": None,
            "data_latency_ms": float(signal.data_latency_ms),
            "risk_state": risk.to_dict(),
        }

        if match is None:
            base["reject_reason"] = "mechanism is not PAPER_ELIGIBLE"
            return base

        cost_bps = match.cost.round_trip_bps()
        base["cost_bps_est"] = cost_bps
        base["net_edge_bps_est"] = float(signal.gross_edge_bps_est) - cost_bps

        budget = match.spec.max_data_latency_ms()
        if signal.data_latency_ms > budget:
            base["reject_reason"] = (
                "data latency %.0f ms over the %.0f ms budget"
                % (signal.data_latency_ms, budget)
            )
            return base

        if signal.gross_edge_bps_est - self.cost_multiple_required * cost_bps <= 0:
            base["reject_reason"] = "net_edge_bps_est <= 0 after cost_x2"
            return base

        breach = self.limits.breach(risk, signal.notional)
        if breach:
            base["reject_reason"] = breach
            return base

        base["decision"] = "PAPER_ACCEPT"
        base["position_notional_paper"] = float(signal.notional)
        return base
