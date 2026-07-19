"""
src/institutional/portfolio/listing_age_gate.py
─────────────────────────────────────────────────────────────────────────────
Gate d'âge de listing : AUCUN LONG sur un perp listé depuis < N jours.

Preuve (event study 518 listings 2023→2026, reports/LISTING_EVENT_STUDY.md +
test J+22→J+30) : drift post-listing négatif net de coûts ×2 sur TOUTES les
fenêtres mesurées jusqu'à J+30 (médiane −285 bps même sur J+22→J+30, négative
sur les 4 cohortes annuelles). Le filtre est mesuré de bout en bout — aucune
extrapolation. Décision utilisateur 2026-07-18.

Point-in-time propre : l'âge est calculé depuis `onboardDate` (heure exacte
d'ouverture du contrat, connue à l'avance) — aucun lookahead possible.

Source : data/listings_backfill/binance/listings_calendar.parquet
(régénérée par scripts/backfill_binance_perp_listings.py). Un symbole absent
du calendrier est BLOQUÉ (conservateur : absent = probablement listé après la
dernière régénération, donc jeune).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_CALENDAR = (Path(__file__).resolve().parents[3]
                    / "data" / "listings_backfill" / "binance" / "listings_calendar.parquet")


class ListingAgeGate:
    """Bloque tout long sur un actif listé depuis moins de `min_age_days` jours."""

    def __init__(self, min_age_days: int = 30,
                 calendar_path: Optional[Path] = None):
        self.min_age_days = min_age_days
        self._onboard: Dict[str, pd.Timestamp] = {}
        path = Path(calendar_path) if calendar_path is not None else DEFAULT_CALENDAR
        if path.exists():
            cal = pd.read_parquet(path)
            ok = cal["onboard_ts"].notna()
            self._onboard = dict(zip(cal.loc[ok, "symbol"], cal.loc[ok, "onboard_ts"]))
        else:
            logger.warning("ListingAgeGate: calendrier absent (%s) — TOUT est bloqué ; "
                           "lancer scripts/backfill_binance_perp_listings.py", path)

    @property
    def n_known(self) -> int:
        return len(self._onboard)

    def allows(self, asset: str, ts) -> bool:
        onboard = self._onboard.get(asset)
        if onboard is None:
            return False        # inconnu = probablement plus jeune que le calendrier
        age = pd.Timestamp(ts) - onboard
        return age >= pd.Timedelta(days=self.min_age_days)
