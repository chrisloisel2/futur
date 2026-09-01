"""
src/institutional/live_alpha_lab/trade_trace.py
─────────────────────────────────────────────────────────────────────────────
Reconstruction de trace complète (item P0.3, phase CLOSE THE EXECUTION
LOOP) : répond à "pourquoi cette position existe-t-elle" en remontant la
chaîne RÉELLEMENT traçable dans le pipeline actuel :

  decision (decisions.parquet, alpha_id + timestamp + symbol)
    -> intent (PortfolioIntent -- même triplet ; intent_id/signal_id
       stampés sur l'ordre dès sa création, cf orders.py, item P0.2)
    -> intent_ledger row (portfolio_target/executed_delta agrégés à ce ts,
       tous les alphas concurrents visibles via `alpha_intents`)
    -> order (ShadowOrder, state.orders)
    -> fill (ShadowFill, state.fills)
    -> position (Position, state.positions -- ou déjà clôturée si absente)

⚠ raw_event_id / feature_snapshot_id : ces identifiants n'existent PAS
dans le pipeline actuel -- chaque moteur d'alpha recalcule ses features à
la volée à partir des données brutes sans stamper d'ID par événement/
snapshot. Ils sont retournés explicitement "NOT_AVAILABLE" avec la raison
plutôt que fabriqués -- discipline anti-fabrication du projet (cf
marks.py : jamais un prix inventé ; même principe pour un ID inventé).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
LAB_DIR = ROOT / "reports" / "live_alpha_lab"
PORTFOLIOS_DIR = LAB_DIR / "portfolios"

# mêmes mappings que scripts/compute_live_alpha_lab_scoreboard.py -- colonne
# "symbole" pas toujours `symbol` (SHORT_COVERING utilise `asset`, hérité du
# schéma Opportunity), colonne temps pas toujours `event_time`. Jamais deviné.
_TIME_COL = {
    "LIQ_CASCADE_REPEAT_V1": "event_time", "LIQ_CASCADE_FAR_FROM_LOW_V1": "event_time",
    "SHORT_COVERING_CONTINUATION_V1": "timestamp", "WHALE_LSR_SCREEN_V1": "timestamp",
    "FUNDING_BASIS_DISAGREEMENT_V1": "date", "FUNDING_BASIS_DISAGREEMENT_V2": "date",
    "CROSS_SECTIONAL_MOMENTUM_LIVE_V1": "event_time",
    "CROSS_SECTIONAL_MOMENTUM_LIVE_V2": "event_time", "VOL_FORECAST_LAYER_V1": "event_time",
}
_SYMBOL_COL = {
    "LIQ_CASCADE_REPEAT_V1": "symbol", "LIQ_CASCADE_FAR_FROM_LOW_V1": "symbol",
    "SHORT_COVERING_CONTINUATION_V1": "asset", "WHALE_LSR_SCREEN_V1": "symbol",
    "FUNDING_BASIS_DISAGREEMENT_V1": "symbol", "FUNDING_BASIS_DISAGREEMENT_V2": "symbol",
    "CROSS_SECTIONAL_MOMENTUM_LIVE_V1": "symbol",
    "CROSS_SECTIONAL_MOMENTUM_LIVE_V2": "symbol", "VOL_FORECAST_LAYER_V1": None,
}


def _load_state(portfolio_name: str) -> Dict[str, Any]:
    p = PORTFOLIOS_DIR / portfolio_name / "state.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def _load_intent_ledger(portfolio_name: str) -> pd.DataFrame:
    p = PORTFOLIOS_DIR / portfolio_name / "intent_ledger.parquet"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_parquet(p)


def find_decision_row(alpha_id: str, instrument: str, ts_iso: str) -> Optional[Dict[str, Any]]:
    """Retrouve la ligne decisions.parquet source d'un intent -- match exact
    sur (symbol_col==instrument, time_col==ts). Retourne None si l'alpha
    n'a pas de mapping colonne connu, si le fichier n'existe pas, ou si
    aucune ligne ne correspond (jamais un "à peu près" silencieux)."""
    time_col = _TIME_COL.get(alpha_id)
    symbol_col = _SYMBOL_COL.get(alpha_id)
    if time_col is None or symbol_col is None:
        return None
    p = LAB_DIR / alpha_id / "decisions.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    if time_col not in df.columns or symbol_col not in df.columns:
        return None
    ts = pd.Timestamp(ts_iso)
    match = df[(df[symbol_col] == instrument) & (pd.to_datetime(df[time_col], utc=True) == ts)]
    if match.empty:
        return None
    return json.loads(match.iloc[0].to_json())


def reconstruct(portfolio_name: str, instrument: str) -> Dict[str, Any]:
    """Chaîne causale complète pour (portfolio_name, instrument) : tous les
    ordres/fills jamais émis sur cet instrument dans ce portefeuille, la
    position courante (si toujours ouverte), et pour chaque ordre la
    décision d'alpha source (si retrouvable)."""
    state = _load_state(portfolio_name)
    orders = [o for o in state.get("orders", []) if o.get("symbol") == instrument]
    fills = [f for f in state.get("fills", []) if f.get("symbol") == instrument]
    position = state.get("positions", {}).get(instrument)

    ledger = _load_intent_ledger(portfolio_name)
    ledger_rows = []
    if not ledger.empty:
        ledger_rows = ledger[ledger["instrument"] == instrument].to_dict("records")

    steps: List[Dict[str, Any]] = []
    for order in sorted(orders, key=lambda o: o.get("timestamp_submit", "")):
        order_fills = [f for f in fills if f.get("order_id") == order.get("order_id")]
        decision = find_decision_row(order["alpha_id"], instrument, order["timestamp_decision"])
        ledger_row = next(
            (r for r in ledger_rows if r.get("ts") == order.get("timestamp_decision")), None
        )
        steps.append({
            "order": order,
            "fills": order_fills,
            "intent_ledger_row": ledger_row,
            "decision": decision,
            "decision_found": decision is not None,
            "raw_event_id": "NOT_AVAILABLE",
            "raw_event_id_reason": (
                "le pipeline actuel ne stampe pas d'identifiant par événement brut "
                "-- les features sont recalculées à la volée par chaque moteur d'alpha"
            ),
            "feature_snapshot_id": "NOT_AVAILABLE",
            "feature_snapshot_id_reason": (
                "idem raw_event_id -- pas de snapshot de features persisté avec un ID propre, "
                "seules les valeurs finales (score_raw, expected_return, etc.) sont dans decisions.parquet"
            ),
        })

    return {
        "portfolio_id": portfolio_name,
        "instrument": instrument,
        "position_id": f"{portfolio_name}:{instrument}",
        "current_position": position,
        "n_orders": len(orders),
        "n_fills": len(fills),
        "n_decisions_found": sum(1 for s in steps if s["decision_found"]),
        "n_decisions_not_found": sum(1 for s in steps if not s["decision_found"]),
        "steps": steps,
    }


def narrate(trace: Dict[str, Any]) -> str:
    """Résumé texte lisible -- pas une structure JSON à parser à la main."""
    lines = [
        f"Position {trace['instrument']} dans {trace['portfolio_id']} "
        f"({trace['n_orders']} ordre(s), {trace['n_fills']} fill(s)) :",
    ]
    if trace["current_position"]:
        p = trace["current_position"]
        lines.append(
            f"  État courant : quantity={p['quantity']:.6f} entry_price={p['entry_price']:.6f} "
            f"owner_alpha={p.get('owner_alpha')} realized_pnl={p.get('realized_pnl', 0):.4f}"
        )
    else:
        lines.append("  État courant : AUCUNE position ouverte (clôturée ou jamais ouverte)")
    for i, step in enumerate(trace["steps"]):
        o = step["order"]
        d = step["decision"]
        d_str = (f"decision trouvée (score_net={d.get('score_net')}, "
                 f"reason={d.get('reason')})" if d else "decision NON retrouvée")
        lines.append(
            f"  [{i}] {o['timestamp_submit']} order={o['order_id']} status={o['status']} "
            f"alpha={o['alpha_id']} side={o['side']} filled={o['filled_quantity']:.6f}/"
            f"{o['requested_quantity']:.6f} @ {o.get('fill_price')} -- {d_str}"
        )
    return "\n".join(lines)
