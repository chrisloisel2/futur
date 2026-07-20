"""
src/alpha20/tournament/paper_account.py — compte paper ISOLÉ d'un runner.

Chaque runner du tournoi possède : cash/NAV (net_nav sur sa chaîne isolée),
positions (état déclaratif tenu par l'adaptateur, PAS le compte), ledger
hash-chaîné dédié (accounting.event_ledger paramétré par
`event_ledger.runner_ledger_dir(runner_id)`), frais/funding/borrow/taxes
(mêmes kinds que le ledger portefeuille), état de risque + kill switch
(profil ALPHA20_LOW_RISK unifié, mêmes seuils que la mission : kill -2,5 %,
marge 20 %, venue 15 %, ES99 0,5 %, jambe nue 30 s).

L'historique de NAV pour le drawdown/ES99 vient des événements `mark` (
amount_usdt=0.0, meta={"nav_usdt":…}) — jamais un fichier séparé qui pourrait
diverger du ledger.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from src.alpha20.accounting import event_ledger, net_nav
from src.alpha20.contracts import LedgerEvent
from src.alpha20.risk import global_governor as gg


class PaperAccount:
    def __init__(self, runner_id: str, capital_eur: float):
        self.runner_id = runner_id
        self.capital_eur = capital_eur
        self.ledger_dir = event_ledger.runner_ledger_dir(runner_id)

    # ── écriture ──────────────────────────────────────────────────────────
    def emit(self, events: Iterable[LedgerEvent]) -> List[str]:
        tagged = []
        for e in events:
            e.meta = dict(e.meta or {}, runner_id=self.runner_id)
            tagged.append(e)
        return event_ledger.append(tagged, ledger_dir=self.ledger_dir)

    def mark(self, nav_usdt: float, extra_meta: Optional[dict] = None,
            ts: Optional[str] = None) -> None:
        """`ts` : réservé au rejeu/à la ré-ingestion d'un fait passé daté —
        en cycle normal, ne JAMAIS le passer (défaut = maintenant)."""
        meta = dict(extra_meta or {}, nav_usdt=round(nav_usdt, 6))
        self.emit([LedgerEvent(
            ts=ts or datetime.now(timezone.utc).isoformat(), kind="mark",
            sleeve="account", venue="offchain", amount_usdt=0.0,
            ref="periodic_mark", meta=meta)])

    # ── lecture ───────────────────────────────────────────────────────────
    def read(self, kinds: Optional[List[str]] = None) -> pd.DataFrame:
        return event_ledger.read(kinds=kinds, ledger_dir=self.ledger_dir)

    def nav_usdt(self) -> float:
        return net_nav.nav(self.capital_eur, ledger_dir=self.ledger_dir)

    def integrity(self) -> dict:
        return event_ledger.integrity(self.ledger_dir)

    def nav_history(self) -> pd.Series:
        df = self.read(kinds=["mark"])
        if df.empty:
            return pd.Series(dtype=float)
        ts = pd.to_datetime(df["ts"], utc=True)
        nav = df["meta"].apply(lambda m: (m or {}).get("nav_usdt"))
        s = pd.Series(nav.values, index=ts).dropna().astype(float).sort_index()
        return s

    def daily_returns(self) -> pd.Series:
        """Rendements sur grille QUOTIDIENNE (dernier mark du jour). Deux
        marks du même cycle (voire deux runners différents) ne partagent
        jamais un timestamp exact — toute statistique inter-séries (corrélation,
        Sharpe annualisé en √365, DSR, bootstrap) doit passer par ici, jamais
        par un pct_change() brut sur les timestamps de mark."""
        h = self.nav_history()
        if len(h) < 2:
            return pd.Series(dtype=float)
        daily = h.resample("1D").last().dropna()
        return daily.pct_change().dropna()

    def drawdown(self) -> float:
        h = self.nav_history()
        if len(h) < 2:
            return 0.0
        peak = h.cummax()
        dd = (h - peak) / peak
        return float(-dd.iloc[-1])          # positif = perte depuis le pic

    def es99_1d(self) -> Optional[float]:
        h = self.nav_history()
        if len(h) < 100:
            return None
        r = h.pct_change().dropna()
        if len(r) < 100:
            return None
        var = np.quantile(r, 0.01)
        tail = r[r <= var]
        return float(-tail.mean()) if len(tail) else float(-var)

    def n_events(self) -> int:
        return len(self.read())

    def age_days(self) -> float:
        df = self.read()
        if df.empty:
            return 0.0
        t0 = pd.Timestamp(df["ts"].min())
        return (pd.Timestamp.now(tz="UTC") - t0).total_seconds() / 86400.0

    def risk_metrics(self, gross_usdt: float, net_delta_usdt: float,
                     venue_unsecured_frac: Dict[str, float],
                     naked_leg_age_s: float = 0.0) -> dict:
        nav = self.nav_usdt()
        return {
            "drawdown": self.drawdown(),
            "daily_loss": max(-self._pct_change(pd.Timedelta("1d")), 0.0),
            "weekly_loss": max(-self._pct_change(pd.Timedelta("7d")), 0.0),
            "es99_1d": self.es99_1d() or 0.0,
            "net_delta": abs(net_delta_usdt) / nav if nav else 0.0,
            "margin_used": gross_usdt * 0.10 / nav if nav else 0.0,   # proxy IM 10%
            "venue_unsecured_max": max(venue_unsecured_frac.values() or [0.0]),
            "naked_leg_age_s": naked_leg_age_s,
        }

    def _pct_change(self, window: pd.Timedelta) -> float:
        h = self.nav_history()
        if len(h) < 2:
            return 0.0
        cutoff = h.index[-1] - window
        base = h[h.index <= cutoff]
        if base.empty:
            return 0.0
        return float(h.iloc[-1] / base.iloc[-1] - 1)

    def evaluate_risk(self, **kw) -> "gg.GovernorDecision":
        return gg.evaluate(self.risk_metrics(**kw))
