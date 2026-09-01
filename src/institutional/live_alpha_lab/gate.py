"""
src/institutional/live_alpha_lab/gate.py
─────────────────────────────────────────────────────────────────────────────
WHALE_LSR_SCREEN_V1 n'émet pas de position -- c'est un GATE qui réduit/bloque
les intents LONG d'AUTRES alphas sur le même instrument au même moment
("éviter les nouveaux LONGS", jamais un short direct -- SHORT_REJECTED).
"""
from __future__ import annotations

import pandas as pd


def active_screen_symbols(decisions_forward_only: pd.DataFrame, as_of: pd.Timestamp,
                          lookback_hours: float = 24.0) -> set:
    """Symboles sous screen_flag=True dans la fenêtre [as_of - lookback, as_of]
    -- un screen récent reste actif un moment, pas juste à l'instant exact où
    il a été émis (le mécanisme mesure une sous-performance sur fwd_24h)."""
    if decisions_forward_only.empty:
        return set()
    df = decisions_forward_only
    window = df[(df["timestamp"] <= as_of)
               & (df["timestamp"] >= as_of - pd.Timedelta(hours=lookback_hours))
               & (df["screen_flag"] == True)]  # noqa: E712
    return set(window["symbol"].unique())


def apply_screen(target_position_fraction: float, instrument: str, direction: str,
                 screened_symbols: set) -> float:
    """Réduit à 0 tout intent LONG sur un instrument sous screen actif.
    N'affecte jamais un SHORT (qui n'existe de toute façon pas -- long-only)."""
    if direction == "LONG" and instrument in screened_symbols:
        return 0.0
    return target_position_fraction
