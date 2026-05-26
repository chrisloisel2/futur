# -*- coding: utf-8 -*-
"""
ai/level_1/macro_gate.py — GATE MACRO DYNAMIQUE
=================================================

Exploite les features cross-macro du bundle hedge_fund pour ajuster
dynamiquement les seuils de decision du pipeline.

Logique :
  - Quand le macro est bullish (OI monte, funding positif, greed, crowd long)
    → baisser le seuil direction LONG (plus facile d'entrer)
    → relever le seuil direction SHORT (plus difficile de shorter)
  - Quand le macro est bearish (foule extreme, OI distribution, fear)
    → baisser le seuil direction SHORT
    → relever le seuil direction LONG
  - En zone neutre : pas d'ajustement

API publique :
  MacroGate.from_df(df)           — cree depuis le dataframe pipeline
  MacroGate.adjust_long_thr(base) — retourne le seuil long ajuste
  MacroGate.adjust_short_thr(base) — retourne le seuil short ajuste
  MacroGate.score                 — score composite [-2, +2]
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


# Parametres de sensibilite de l'ajustement de seuil
_LONG_BOOST_MAX  = 0.06   # reduction max du seuil long (macro tres bullish)
_LONG_PENALTY    = 0.04   # hausse max du seuil long (macro tres bearish)
_SHORT_BOOST_MAX = 0.06   # reduction max du seuil short (macro tres bearish)
_SHORT_PENALTY   = 0.04   # hausse max du seuil short (macro tres bullish)

# Seuils d'activation (score doit depasser pour ajuster)
_BULLISH_ACTIVATION  =  0.4   # score > 0.4 pour activer le boost long
_BEARISH_ACTIVATION  = -0.4   # score < -0.4 pour activer le boost short
_STRONG_SIGNAL       =  1.2   # score > 1.2 = signal macro fort (boost maximal)


@dataclass
class MacroGate:
    """
    Encapsule le score macro et les ajustements de seuil associes.

    score     : float ∈ [-2, +2]
                +2 = macro tres bullish (OI haut, funding positif, greed, crowd long)
                -2 = macro tres bearish (crowd extreme, OI distribution, fear)
                 0 = neutre, pas d'ajustement

    confluence_long  : int ∈ [0, 5] — nb signaux bull alignes
    confluence_short : int ∈ [0, 5] — nb signaux bear alignes
    """
    score:            float = 0.0
    confluence_long:  float = 0.0
    confluence_short: float = 0.0
    oi_accel:         float = 0.0

    @classmethod
    def from_df(cls, df: pd.DataFrame, bar_idx: int = -1) -> "MacroGate":
        """
        Construit un MacroGate depuis le dataframe pipeline a un instant donne.

        bar_idx : -1 = derniere barre (defaut), ou index specifique
        """
        def _get(col: str) -> float:
            if col in df.columns:
                val = df[col].iloc[bar_idx]
                return float(val) if not (val != val) else 0.0  # NaN check
            return 0.0

        score           = _get("macro_regime_score")
        conf_long       = _get("macro_confluence_long")
        conf_short      = _get("macro_confluence_short")
        oi_accel        = _get("oi_acceleration_z")

        return cls(
            score            = float(np.clip(score, -2.0, 2.0)),
            confluence_long  = float(conf_long),
            confluence_short = float(conf_short),
            oi_accel         = float(oi_accel),
        )

    @classmethod
    def from_row(cls, row: "pd.Series") -> "MacroGate":
        """Construit depuis une Series (une ligne du df)."""
        def _get(col: str) -> float:
            v = row.get(col, 0.0)
            if v is None or (isinstance(v, float) and np.isnan(v)):
                return 0.0
            return float(v)
        return cls(
            score            = float(np.clip(_get("macro_regime_score"), -2.0, 2.0)),
            confluence_long  = _get("macro_confluence_long"),
            confluence_short = _get("macro_confluence_short"),
            oi_accel         = _get("oi_acceleration_z"),
        )

    @classmethod
    def neutral(cls) -> "MacroGate":
        return cls(score=0.0, confluence_long=0.0, confluence_short=0.0, oi_accel=0.0)

    # ── Ajustement de seuils ────────────────────────────────────────────────

    def adjust_long_thr(self, base: float) -> float:
        """
        Retourne le seuil direction LONG ajuste.
        Macro bullish → seuil bas (plus facile d'entrer)
        Macro bearish → seuil haut (plus difficile)
        """
        if self.score >= _BULLISH_ACTIVATION:
            # Macro bullish : boost proportionnel au score
            strength = min((self.score - _BULLISH_ACTIVATION) / (_STRONG_SIGNAL - _BULLISH_ACTIVATION), 1.0)
            adj = -_LONG_BOOST_MAX * strength
            # Bonus si OI accele aussi
            if self.oi_accel > 0.5:
                adj -= 0.01
            # Bonus si confluence forte
            if self.confluence_long >= 3:
                adj -= 0.01
        elif self.score <= _BEARISH_ACTIVATION:
            # Macro bearish : penalise le long
            strength = min((abs(self.score) - abs(_BEARISH_ACTIVATION)) / (_STRONG_SIGNAL - abs(_BEARISH_ACTIVATION)), 1.0)
            adj = _LONG_PENALTY * strength
        else:
            adj = 0.0

        return float(np.clip(base + adj, 0.40, 0.90))

    def adjust_short_thr(self, base: float) -> float:
        """
        Retourne le seuil direction SHORT ajuste.
        Macro bearish → seuil bas (plus facile de shorter)
        Macro bullish → seuil haut (plus difficile)
        """
        if self.score <= _BEARISH_ACTIVATION:
            # Macro bearish : boost du short
            strength = min((abs(self.score) - abs(_BEARISH_ACTIVATION)) / (_STRONG_SIGNAL - abs(_BEARISH_ACTIVATION)), 1.0)
            adj = -_SHORT_BOOST_MAX * strength
            # Bonus confluence bearish
            if self.confluence_short >= 3:
                adj -= 0.01
        elif self.score >= _BULLISH_ACTIVATION:
            # Macro bullish : penalise le short
            strength = min((self.score - _BULLISH_ACTIVATION) / (_STRONG_SIGNAL - _BULLISH_ACTIVATION), 1.0)
            adj = _SHORT_PENALTY * strength
        else:
            adj = 0.0

        return float(np.clip(base + adj, 0.45, 0.90))

    def is_bullish(self, threshold: float = _BULLISH_ACTIVATION) -> bool:
        return self.score >= threshold

    def is_bearish(self, threshold: float = abs(_BEARISH_ACTIVATION)) -> bool:
        return self.score <= -threshold

    def position_size_multiplier(self) -> float:
        """
        Multiplicateur de taille de position base sur la force du signal macro.
        1.0 = neutre, 1.25 = macro fort, 0.75 = macro oppose

        Utilise dans Level 7 (risk sizing) si disponible.
        """
        abs_score = abs(self.score)
        if abs_score < 0.3:
            return 1.0
        elif abs_score < 0.8:
            return 1.10
        elif abs_score < 1.5:
            return 1.20
        else:
            return 1.30

    def __repr__(self) -> str:
        direction = "BULL" if self.score > 0.3 else ("BEAR" if self.score < -0.3 else "NEUT")
        return (
            f"MacroGate(score={self.score:.2f} [{direction}] "
            f"conf_L={self.confluence_long:.0f} conf_S={self.confluence_short:.0f} "
            f"oi_accel={self.oi_accel:.2f})"
        )


def compute_macro_gate_series(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule une colonne 'macro_gate_score' pour tout le DataFrame.
    Utile pour le backtest vectorise (pas d'iterrows).

    Retourne df avec la colonne ajoutee.
    """
    df = df.copy()

    def _col(name: str) -> pd.Series:
        if name in df.columns:
            return pd.to_numeric(df[name], errors="coerce").fillna(0.0)
        return pd.Series(0.0, index=df.index)

    score = _col("macro_regime_score")
    df["macro_gate_score"] = score.clip(-2.0, 2.0)
    return df
