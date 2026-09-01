"""
src/institutional/live_alpha_lab/compare_portfolios.py
─────────────────────────────────────────────────────────────────────────────
Deterministic diff tool entre deux PortfolioState persistés. Ne compare
JAMAIS seulement les summaries finaux : rejoue l'historique aligné
(intent_ledger puis equity_curve) et retourne la PREMIERE divergence
exacte, avec timestamp/field/value_A/value_B et l'objet causal amont
(instrument concerné, mark_source si dérivable) -- jamais une conclusion
"ça diverge quelque part".
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
PORTFOLIOS_DIR = ROOT / "reports" / "live_alpha_lab" / "portfolios"

# champs de equity_curve comparés, dans l'ordre où on les rapporte
_EQUITY_FIELDS = [
    "status", "n_positions", "gross_exposure", "net_exposure",
    "realized_pnl", "unrealized_pnl", "fees", "funding", "equity", "drawdown",
]


@dataclass
class Divergence:
    stage: str            # "intent_ledger" | "equity_curve"
    timestamp: str
    field: str
    value_a: Any
    value_b: Any
    causal_instrument: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage, "timestamp": self.timestamp, "field": self.field,
            "value_a": self.value_a, "value_b": self.value_b,
            "causal_instrument": self.causal_instrument,
        }


def _load_intent_ledger(name: str) -> pd.DataFrame:
    p = PORTFOLIOS_DIR / name / "intent_ledger.parquet"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_parquet(p)


def _load_state(name: str) -> Dict[str, Any]:
    p = PORTFOLIOS_DIR / name / "state.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def compare_portfolios(portfolio_a: str, portfolio_b: str) -> Optional[Divergence]:
    """Retourne la PREMIERE divergence trouvée (stage='intent_ledger' d'abord,
    car c'est en amont causalement de equity_curve), ou None si les deux
    portefeuilles sont identiques sur tout l'historique persisté.

    Étape 1 -- intent_ledger : la couche d'agrégation/targeting est-elle
    identique (mêmes intents en entrée => même portfolio_target/executed_delta
    par (ts, instrument)) ?
    Étape 2 -- equity_curve : si l'étape 1 est identique partout, la couche
    de step()/MTM (entry_price, marks, PnL) a-t-elle quand même divergé ?
    """
    la = _load_intent_ledger(portfolio_a)
    lb = _load_intent_ledger(portfolio_b)
    if not la.empty and not lb.empty:
        merged = la.merge(
            lb, on=["ts", "instrument"], how="outer",
            suffixes=("_a", "_b"), indicator=True,
        )
        merged = merged.sort_values("ts")
        for _, row in merged.iterrows():
            if row["_merge"] != "both":
                return Divergence(
                    stage="intent_ledger", timestamp=str(row["ts"]),
                    field="row_presence",
                    value_a="present" if row["_merge"] != "right_only" else "absent",
                    value_b="present" if row["_merge"] != "left_only" else "absent",
                    causal_instrument=row["instrument"],
                )
            for field in ("portfolio_target", "executed_delta"):
                va, vb = row.get(f"{field}_a"), row.get(f"{field}_b")
                if pd.notna(va) or pd.notna(vb):
                    if va != vb and not (pd.isna(va) and pd.isna(vb)):
                        return Divergence(
                            stage="intent_ledger", timestamp=str(row["ts"]),
                            field=field, value_a=va, value_b=vb,
                            causal_instrument=row["instrument"],
                        )

    sa = _load_state(portfolio_a)
    sb = _load_state(portfolio_b)
    ea = sa.get("equity_curve", [])
    eb = sb.get("equity_curve", [])
    n = min(len(ea), len(eb))
    for i in range(n):
        ra, rb = ea[i], eb[i]
        ts = ra.get("ts") or ra.get("timestamp")
        for field in _EQUITY_FIELDS:
            va, vb = ra.get(field), rb.get(field)
            if isinstance(va, float) and isinstance(vb, float):
                if abs(va - vb) > 1e-9:
                    return Divergence(
                        stage="equity_curve", timestamp=str(ts), field=field,
                        value_a=va, value_b=vb,
                    )
            elif va != vb:
                return Divergence(
                    stage="equity_curve", timestamp=str(ts), field=field,
                    value_a=va, value_b=vb,
                )
    if len(ea) != len(eb):
        return Divergence(
            stage="equity_curve", timestamp="n/a", field="n_equity_points",
            value_a=len(ea), value_b=len(eb),
        )
    return None


def main() -> int:
    import sys
    if len(sys.argv) != 3:
        print("usage: compare_portfolios.py <PORTFOLIO_A> <PORTFOLIO_B>")
        return 2
    d = compare_portfolios(sys.argv[1], sys.argv[2])
    if d is None:
        print(f"[compare_portfolios] {sys.argv[1]} == {sys.argv[2]} : identiques sur tout l'historique persisté")
        return 0
    print(f"[compare_portfolios] PREMIERE divergence : {json.dumps(d.as_dict(), indent=2, default=str)}")
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
