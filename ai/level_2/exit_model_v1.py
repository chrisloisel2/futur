"""
ai/level_2/exit_model_v1.py — Exit Fleet v1 : modèle de sortie de position
============================================================================

Architecture : 19 spécialistes contextuels + 1 général
  - 1 général    : entraîné sur tous les samples
  - 18 contextuels : 3 phases × 3 états PnL × 2 régimes marché

Routing au moment de l'inférence :
  Phase     : early (bars_held<=2), mid (bars_held 3-5), late (bars_held>=6)
  PnL state : winning (ret > cost+0.5%), losing (ret < -(cost+0.5%)), breakeven
  Régime    : trending (adx_20>25 OR choppiness_20<0.55), ranging (sinon)

Blend :
  p_out = (1 - spec_w) * p_general + spec_w * p_specialist
  spec_w = min(0.65, (spec.val_auc_ - 0.50) * 3.0)  si val_auc > 0.52
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

from ai.level_0.exit_labels import EXIT_ALL_FEATURES, EXIT_POSITION_FEATURES, EXIT_MARKET_FEATURES
from ai.level_0.constants import COST_PCT

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────

_SPECIALIST_NAMES: Tuple[str, ...] = tuple(
    f"{phase}_{pnl}_{regime}"
    for phase in ("early", "mid", "late")
    for pnl   in ("winning", "breakeven", "losing")
    for regime in ("trending", "ranging")
)

assert len(_SPECIALIST_NAMES) == 18, f"Expected 18 specialists, got {len(_SPECIALIST_NAMES)}"

_MAX_SPW = 60.0   # cap sur le class weight


# ─────────────────────────────────────────────────────────────────────────────
# Routing helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_phase(bars_held: float) -> str:
    if bars_held <= 2:
        return "early"
    elif bars_held <= 5:
        return "mid"
    else:
        return "late"


def _get_pnl_state(unrealized_ret: float, cost_pct: float = COST_PCT) -> str:
    margin = cost_pct + 0.005
    if unrealized_ret > margin:
        return "winning"
    elif unrealized_ret < -margin:
        return "losing"
    else:
        return "breakeven"


def _get_regime(adx_20: float, choppiness_20: float) -> str:
    if adx_20 > 25 or choppiness_20 < 0.55:
        return "trending"
    return "ranging"


def _specialist_key_for_row(row: pd.Series, cost_pct: float = COST_PCT) -> str:
    phase  = _get_phase(float(row.get("bars_held", 1.0)))
    pnl    = _get_pnl_state(float(row.get("unrealized_ret", 0.0)), cost_pct)
    adx    = float(row.get("adx_20", 20.0))
    chop   = float(row.get("choppiness_20", 0.60))
    regime = _get_regime(adx, chop)
    return f"{phase}_{pnl}_{regime}"


def _specialist_mask(df: pd.DataFrame, context_name: str, cost_pct: float = COST_PCT) -> np.ndarray:
    """Retourne un masque booléen des rows appartenant à ce contexte."""
    parts  = context_name.split("_")
    # context_name = phase_pnl_regime (3 parts pour early/mid/late + winning/etc + trending/ranging)
    # Mais "breakeven" et "winning" et "losing" + "trending"/"ranging"
    # Format: {phase}_{pnl}_{regime}
    # Peut avoir 3 tokens: early_winning_trending, late_losing_ranging
    # BUT "breakeven" is one word, so split gives: ['early', 'breakeven', 'trending'] = 3 parts
    # 'early', 'mid', 'late' are single words
    # Reconstruct from the known set
    phase  = parts[0]
    regime = parts[-1]
    pnl    = "_".join(parts[1:-1])  # handles single-word pnl states

    bh   = df["bars_held"].values.astype(np.float64)
    ret  = df["unrealized_ret"].values.astype(np.float64)
    adx  = df["adx_20"].values.astype(np.float64) if "adx_20" in df.columns else np.full(len(df), 20.0)
    chop = df["choppiness_20"].values.astype(np.float64) if "choppiness_20" in df.columns else np.full(len(df), 0.60)

    if phase == "early":
        phase_mask = bh <= 2
    elif phase == "mid":
        phase_mask = (bh >= 3) & (bh <= 5)
    else:  # late
        phase_mask = bh >= 6

    margin = cost_pct + 0.005
    if pnl == "winning":
        pnl_mask = ret > margin
    elif pnl == "losing":
        pnl_mask = ret < -margin
    else:  # breakeven
        pnl_mask = (ret >= -margin) & (ret <= margin)

    if regime == "trending":
        reg_mask = (adx > 25) | (chop < 0.55)
    else:
        reg_mask = (adx <= 25) & (chop >= 0.55)

    return phase_mask & pnl_mask & reg_mask


# ─────────────────────────────────────────────────────────────────────────────
# ExitSpecialist
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ExitSpecialist:
    context_name: str
    features: List[str]
    clf_: Optional[HistGradientBoostingClassifier] = field(default=None, repr=False)
    scaler_: Optional[StandardScaler] = field(default=None, repr=False)
    val_auc_: float = 0.0
    n_train_: int = 0
    n_pos_: int = 0

    def fit(self, df: pd.DataFrame, label_col: str = "y_exit") -> "ExitSpecialist":
        """
        Entraîne le spécialiste sur df (déjà filtré sur son contexte ou global).
        """
        feats = [f for f in self.features if f in df.columns]
        if not feats:
            return self

        y = df[label_col].values.astype(np.int32)
        X = df[feats].fillna(0.0).values.astype(np.float64)

        # Filtrer les labels valides (0 ou 1 seulement)
        valid = (y == 0) | (y == 1)
        X, y = X[valid], y[valid]

        n_pos = int((y == 1).sum())
        n_neg = int((y == 0).sum())
        self.n_train_ = len(y)
        self.n_pos_ = n_pos

        if n_pos < 5 or n_neg < 5:
            return self

        # Class weight balancé, capped
        spw = min(_MAX_SPW, n_neg / max(n_pos, 1))

        is_general = self.context_name == "general"
        if is_general:
            clf = HistGradientBoostingClassifier(
                max_iter=400,
                max_depth=5,
                learning_rate=0.04,
                min_samples_leaf=15,
                class_weight={0: 1.0, 1: spw},
                l2_regularization=1.5,
                random_state=42,
            )
        else:
            clf = HistGradientBoostingClassifier(
                max_iter=280,
                max_depth=5,
                learning_rate=0.05,
                min_samples_leaf=12,
                class_weight={0: 1.0, 1: spw},
                l2_regularization=1.5,
                random_state=42,
            )

        scaler = StandardScaler()
        X_sc = scaler.fit_transform(X)

        clf.fit(X_sc, y)
        self.clf_ = clf
        self.scaler_ = scaler
        return self

    def predict_proba(self, df: pd.DataFrame, mask: np.ndarray) -> np.ndarray:
        """
        Retourne les probabilités de sortie pour les rows sélectionnées par mask.
        Retourne un array de 0.5 si le modèle n'est pas entraîné.
        """
        n_total = len(df)
        result = np.full(n_total, 0.5)

        if self.clf_ is None or self.scaler_ is None:
            return result

        feats = [f for f in self.features if f in df.columns]
        if not feats:
            return result

        idx = np.where(mask)[0]
        if len(idx) == 0:
            return result

        X = df.iloc[idx][feats].fillna(0.0).values.astype(np.float64)
        X_sc = self.scaler_.transform(X)
        proba = self.clf_.predict_proba(X_sc)
        # classe 1 = sortir
        classes = list(self.clf_.classes_)
        if 1 in classes:
            col_1 = classes.index(1)
            result[idx] = proba[:, col_1]
        return result


# ─────────────────────────────────────────────────────────────────────────────
# ExitFleetV1
# ─────────────────────────────────────────────────────────────────────────────

class ExitFleetV1:
    """
    Fleet de modèles de sortie : 1 général + 18 spécialistes contextuels.

    Usage :
        fleet = ExitFleetV1()
        fleet.fit(df_train, df_val)
        p = fleet.predict(df)                         # array de probabilités
        should_exit, p = fleet.should_exit(df_bar, position_state)
    """

    def __init__(self) -> None:
        self.specialists: Dict[str, ExitSpecialist] = {}
        self.features_: List[str] = []
        self.threshold_: float = 0.55
        self.general_w: float = 0.35

    # ── Training ──────────────────────────────────────────────────────────────

    def fit(
        self,
        df_train: pd.DataFrame,
        df_val: Optional[pd.DataFrame] = None,
    ) -> "ExitFleetV1":
        """
        Entraîne l'ensemble de la fleet sur df_train (poolé multi-asset).

        Paramètres
        ----------
        df_train : DataFrame avec EXIT_ALL_FEATURES + 'y_exit'
        df_val   : DataFrame de validation (même format) pour calibrer le threshold
        """
        # Features disponibles
        self.features_ = [f for f in EXIT_ALL_FEATURES if f in df_train.columns]
        print(f"  [ExitFleet] features disponibles: {len(self.features_)}/{len(EXIT_ALL_FEATURES)}")

        # ── Spécialiste général ───────────────────────────────────────────────
        print(f"  [ExitFleet] Entraînement général (n={len(df_train):,})...")
        gen = ExitSpecialist(context_name="general", features=self.features_)
        gen.fit(df_train)
        if gen.clf_ is not None:
            # AUC sur train (indicatif)
            feats = [f for f in self.features_ if f in df_train.columns]
            y_tr  = df_train["y_exit"].values.astype(np.int32)
            valid = (y_tr == 0) | (y_tr == 1)
            if valid.sum() > 10:
                X_tr  = gen.scaler_.transform(df_train[feats].fillna(0.0).values[valid])
                p_tr  = gen.clf_.predict_proba(X_tr)
                cls   = list(gen.clf_.classes_)
                if 1 in cls:
                    p1 = p_tr[:, cls.index(1)]
                    try:
                        gen.val_auc_ = float(roc_auc_score(y_tr[valid], p1))
                    except Exception:
                        gen.val_auc_ = 0.0
            print(f"    général : n={gen.n_train_:,}  pos={gen.n_pos_:,}  "
                  f"train_auc={gen.val_auc_:.4f}")
        else:
            print("    général : ECHEC (pas assez de données)")
        self.specialists["general"] = gen

        # ── Spécialistes contextuels ──────────────────────────────────────────
        for ctx_name in _SPECIALIST_NAMES:
            ctx_mask = _specialist_mask(df_train, ctx_name)
            n_ctx    = int(ctx_mask.sum())

            spec = ExitSpecialist(context_name=ctx_name, features=self.features_)

            if n_ctx >= 30:
                df_ctx = df_train[ctx_mask]
                spec.fit(df_ctx)

                # AUC sur val si disponible
                if df_val is not None and spec.clf_ is not None:
                    val_mask = _specialist_mask(df_val, ctx_name)
                    n_val_ctx = int(val_mask.sum())
                    if n_val_ctx >= 10:
                        feats = [f for f in self.features_ if f in df_val.columns]
                        y_v   = df_val[val_mask]["y_exit"].values.astype(np.int32)
                        valid = (y_v == 0) | (y_v == 1)
                        if valid.sum() >= 10 and len(np.unique(y_v[valid])) > 1:
                            X_v   = spec.scaler_.transform(
                                df_val[val_mask][feats].fillna(0.0).values[valid]
                            )
                            p_v   = spec.clf_.predict_proba(X_v)
                            cls   = list(spec.clf_.classes_)
                            if 1 in cls:
                                p1 = p_v[:, cls.index(1)]
                                try:
                                    spec.val_auc_ = float(roc_auc_score(y_v[valid], p1))
                                except Exception:
                                    spec.val_auc_ = 0.0
                else:
                    # Pas de val : AUC sur train du contexte
                    if spec.clf_ is not None:
                        feats = [f for f in self.features_ if f in df_ctx.columns]
                        y_c   = df_ctx["y_exit"].values.astype(np.int32)
                        valid = (y_c == 0) | (y_c == 1)
                        if valid.sum() >= 10 and len(np.unique(y_c[valid])) > 1:
                            X_c = spec.scaler_.transform(df_ctx[feats].fillna(0.0).values[valid])
                            p_c = spec.clf_.predict_proba(X_c)
                            cls = list(spec.clf_.classes_)
                            if 1 in cls:
                                p1 = p_c[:, cls.index(1)]
                                try:
                                    spec.val_auc_ = float(roc_auc_score(y_c[valid], p1))
                                except Exception:
                                    spec.val_auc_ = 0.0

                print(f"    {ctx_name:<35} n={n_ctx:<6,}  pos={spec.n_pos_:<5,}  "
                      f"auc={spec.val_auc_:.4f}")
            else:
                print(f"    {ctx_name:<35} n={n_ctx:<6,}  SKIP (trop peu de données)")

            self.specialists[ctx_name] = spec

        # ── Calibration du threshold ──────────────────────────────────────────
        if df_val is not None:
            self.threshold_ = self._calibrate_threshold(df_val)
            print(f"\n  [ExitFleet] Threshold calibré: {self.threshold_:.2f}")
        else:
            print(f"\n  [ExitFleet] Pas de val — threshold par défaut: {self.threshold_:.2f}")

        return self

    # ── Inférence ─────────────────────────────────────────────────────────────

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        """
        Retourne les probabilités de sortie pour tous les rows de df.

        Pour chaque row :
          1. p_general calculé par le spécialiste général
          2. Le spécialiste contextuel correspondant est identifié
          3. Blend si val_auc > 0.52 :
             p_out = (1 - spec_w) * p_general + spec_w * p_spec
             où spec_w = min(0.65, (spec.val_auc_ - 0.50) * 3.0)
        """
        gen = self.specialists.get("general")
        if gen is None or gen.clf_ is None:
            return np.full(len(df), 0.5)

        # Probabilités générales pour tous les rows
        all_mask    = np.ones(len(df), dtype=bool)
        p_general   = gen.predict_proba(df, all_mask)
        p_out       = p_general.copy()

        # Blend avec chaque spécialiste
        for ctx_name, spec in self.specialists.items():
            if ctx_name == "general":
                continue
            if spec.clf_ is None:
                continue
            if spec.val_auc_ <= 0.52:
                continue

            spec_w   = min(0.65, (spec.val_auc_ - 0.50) * 3.0)
            ctx_mask = _specialist_mask(df, ctx_name)
            if ctx_mask.sum() == 0:
                continue

            p_spec = spec.predict_proba(df, ctx_mask)
            p_out[ctx_mask] = (1.0 - spec_w) * p_general[ctx_mask] + spec_w * p_spec[ctx_mask]

        return p_out

    def should_exit(
        self,
        df_bar: pd.Series,
        position_state: dict,
    ) -> Tuple[bool, float]:
        """
        Décide si on doit sortir la position maintenant.

        Paramètres
        ----------
        df_bar         : pd.Series avec les features de marché à la barre courante
        position_state : dict avec bars_held, unrealized_ret, max_ret_so_far,
                         min_ret_so_far, drawdown_from_peak, recovery_from_trough,
                         pnl_velocity_1, pnl_velocity_3, pnl_normalized,
                         entry_rsi, entry_adx, entry_trend_score,
                         entry_momentum_score, entry_close_position_in_range

        Retourne
        --------
        (should_exit: bool, p_exit: float)
        """
        # Construire le row complet
        row_dict: dict = {}

        # Features de position depuis position_state
        for feat in EXIT_POSITION_FEATURES:
            row_dict[feat] = float(position_state.get(feat, 0.0))

        # Features de marché depuis df_bar
        for feat in EXIT_MARKET_FEATURES:
            val = df_bar.get(feat, 0.0)
            row_dict[feat] = float(val) if pd.notna(val) else 0.0

        df_single = pd.DataFrame([row_dict])
        p_exit = float(self.predict(df_single)[0])

        return (p_exit >= self.threshold_, p_exit)

    # ── Calibration ───────────────────────────────────────────────────────────

    def _calibrate_threshold(self, df_val: pd.DataFrame) -> float:
        """
        Trouve le threshold qui maximise le net PnL simulé sur df_val.

        Simulation :
          - Grouper par (t0, symbol) → chaque position dans val
          - Pour chaque threshold, choisir la 1ère barre où p >= thr (ou MAX_HOLD)
          - net_pnl = unrealized_ret_à_sortie - cost_pct
          - Comparer avec baseline (hold jusqu'à MAX_HOLD)
        """
        if df_val is None or len(df_val) == 0:
            return 0.55

        if "y_exit" not in df_val.columns or "unrealized_ret" not in df_val.columns:
            return 0.55

        # Calculer p_exit pour tous les rows val
        p_all = self.predict(df_val)

        # Identifier les positions (t0, symbol)
        has_t0  = "t0" in df_val.columns
        has_sym = "symbol" in df_val.columns

        if not has_t0:
            return 0.55

        # Baseline : prendre la dernière barre de chaque position
        if has_sym:
            groups = df_val.groupby(["symbol", "t0"])
        else:
            groups = df_val.groupby("t0")

        # Construire une liste de positions avec leurs P&L
        positions = []
        for grp_key, grp_df in groups:
            grp_idx = grp_df.index.tolist()
            # Ordonner par k
            if "k" in grp_df.columns:
                grp_df_s = grp_df.sort_values("k")
            else:
                grp_df_s = grp_df
            ks        = grp_df_s.index.tolist()
            rets      = grp_df_s["unrealized_ret"].values
            p_vals    = p_all[grp_df_s.index - df_val.index[0]]
            # Handle index offset properly
            p_vals_local = np.array([p_all[df_val.index.get_loc(i)] for i in ks])
            positions.append({
                "ks": ks,
                "rets": rets,
                "p_vals": p_vals_local,
            })

        # P&L baseline (dernière barre)
        def _sim_pnl(positions, thr, cost_pct=COST_PCT):
            total = 0.0
            for pos in positions:
                rets = pos["rets"]
                p_v  = pos["p_vals"]
                # Première barre avec p >= thr
                exit_idx = None
                for j, p in enumerate(p_v):
                    if p >= thr:
                        exit_idx = j
                        break
                if exit_idx is None:
                    exit_idx = len(rets) - 1  # MAX_HOLD
                total += rets[exit_idx] - cost_pct
            return total

        best_thr = 0.55
        best_pnl = -np.inf

        baseline_pnl = _sim_pnl(positions, thr=1.01)  # thr impossible = hold MAX_HOLD

        for thr in np.arange(0.35, 0.75, 0.02):
            pnl = _sim_pnl(positions, thr)
            if pnl > best_pnl:
                best_pnl = pnl
                best_thr = float(thr)

        print(f"  [ExitFleet] calibration : baseline_pnl={baseline_pnl:.4f}  "
              f"best_thr={best_thr:.2f}  best_pnl={best_pnl:.4f}  "
              f"improvement={best_pnl - baseline_pnl:+.4f}")

        return best_thr
