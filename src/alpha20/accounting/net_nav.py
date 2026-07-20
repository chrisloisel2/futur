"""
src/alpha20/accounting/net_nav.py — NAV et R_net depuis le ledger, rien d'autre.

R_net = (PnL_marché + carry + basis − fees − slippage − impact − borrow − gas
         − infra − provision fiscale) / NAV_début

Convention : chaque LedgerEvent porte amount_usdt SIGNÉ (+ entre dans la NAV).
Les coûts sont donc déjà négatifs dans le ledger ; ici on ne fait qu'agréger
et décomposer — aucune hypothèse, aucun agrégat externe.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from src.alpha20.accounting import event_ledger

PNL_KINDS = ("fill", "funding", "mark")          # marché + carry/basis réalisés
COST_KINDS = ("fee", "borrow", "gas", "infra", "transfer")
TAX_KINDS = ("tax_provision",)


def nav(initial_usdt: float, until: Optional[str] = None,
       ledger_dir: Optional[Path] = None) -> float:
    df = event_ledger.read(ledger_dir=ledger_dir)
    if df.empty:
        return initial_usdt
    if until:
        df = df[df["ts"] <= until]
    flows = df[df["kind"].isin(PNL_KINDS + COST_KINDS + TAX_KINDS)]
    return initial_usdt + float(flows["amount_usdt"].sum())


def r_net(nav_start_usdt: float, since: str, until: Optional[str] = None,
         ledger_dir: Optional[Path] = None) -> Dict:
    """Décomposition complète du rendement net sur [since, until]."""
    df = event_ledger.read(since=since, ledger_dir=ledger_dir)
    if until is not None and not df.empty:
        df = df[df["ts"] <= until]
    if df.empty:
        return {"r_net": 0.0, "by_kind": {}, "nav_start": nav_start_usdt}
    by_kind = df.groupby("kind")["amount_usdt"].sum().to_dict()
    pnl = sum(by_kind.get(k, 0.0) for k in PNL_KINDS)
    costs = sum(by_kind.get(k, 0.0) for k in COST_KINDS)      # déjà négatifs
    tax = sum(by_kind.get(k, 0.0) for k in TAX_KINDS)
    net = pnl + costs + tax
    return {
        "r_net": net / nav_start_usdt,
        "r_gross": pnl / nav_start_usdt,
        "cost_drag": -costs / nav_start_usdt,
        "tax_drag": -tax / nav_start_usdt,
        "by_kind": {k: round(float(v), 4) for k, v in by_kind.items()},
        "by_sleeve": {k: round(float(v), 4) for k, v in
                      df.groupby("sleeve")["amount_usdt"].sum().to_dict().items()},
        "nav_start": nav_start_usdt,
        "chain_ok": event_ledger.verify_chain(ledger_dir),
    }
