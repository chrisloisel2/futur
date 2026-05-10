"""
level_0/features.py — LISTES DE FEATURES PAR BRANCHE
=====================================================

Trois groupes distincts et explicites :
  FEATURES_COMMON  : features utilisées par le FILTRE, LONG et SHORT
  FEATURES_LONG    : COMMON + features favorables au long (momentum, trend)
  FEATURES_SHORT   : COMMON + features favorables au short (reversal, surachat)

Règle : ne jamais supposer que long et short ont les mêmes features importantes.

Pour ajouter une feature :
  1. L'ajouter dans le groupe le plus pertinent
  2. Vérifier qu'elle existe dans le CSV de référence
  3. Relancer les backtests avant/après pour confirmer l'impact
"""
from __future__ import annotations

from typing import List
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# FEATURES MACRO — funding, open interest, long/short ratios, sentiment, news
# ─────────────────────────────────────────────────────────────────────────────
# Disponibles uniquement quand le bundle parquet est utilisé comme source.
# Ignorées silencieusement en live si non calculées (validate_features garde).
# Ces features ajoutent un signal de second ordre (positionnement + sentiment)
# qui est orthogonal au price-action pur de FEATURES_COMMON.

FEATURES_MACRO_REGIME: List[str] = [
    # Signaux structurels longs (72h+) — corrélés avec les régimes bull/bear durables
    "fear_greed_value_z_72",          # sentiment persistant : peur = bear structurel
    "funding_rate_z_72",              # funding négatif durable = short pressure bear
    "oihist_sumOpenInterest_z_72",    # OI en expansion = tendance confirme
    "global_ls_longShortRatio_z_72",  # positioning institutional moyen terme
]

FEATURES_MACRO_LONG: List[str] = [
    # Signaux bull court terme (24h) — confirment l'élan haussier
    "funding_rate_z_24",           # funding positif = bullish crowd
    "oihist_sumOpenInterest_z_24", # OI monte avec le prix = confirmation
    "fear_greed_value_z_24",       # greed = momentum sentiment
    "taker_ls_imbalance",          # acheteurs agressifs > vendeurs
    "oi_x_fng",                    # OI × F&G : double confirmation sentiment+positions
]

FEATURES_MACRO_SHORT: List[str] = [
    # Signaux de foule extrême et épuisement — contra indicators pour le short
    "funding_rate_z_24",           # funding trop positif = foule longée → fade
    "funding_x_global_ls",         # foule doublement longée (funding + L/S)
    "fear_greed_value_z_24",       # greed extrême = retournement imminent
    "taker_ls_buySellRatio_z_24",  # ratio taker z-scoré (divergence = top)
    "global_ls_longShortRatio_z_24", # L/S élevé = positionnement contrarian
]


# ─────────────────────────────────────────────────────────────────────────────
# FEATURES COMMUNES — filtre, long, short
# ─────────────────────────────────────────────────────────────────────────────
# Ces features décrivent le contexte de marché indépendamment de la direction.

FEATURES_COMMON: List[str] = [
    # ── Groupe 1 : Structure de prix ─────────────────────────────────────────
    "rv_12", "rv_24", "rv_48", "rv_72", "rv_168",
    "rv_ratio_24_72",       # régime vol : court/long
    "rv_ratio_12_48",
    "atr_pct_14",
    "boll_width_20",
    "boll_pos_20",
    "close_in_bar",
    "intrabar_range_pct",
    "eff_ratio_12",
    "eff_ratio_24",
    "zscore_close_24",
    # ── Groupe 2 : Flow / microstructure ─────────────────────────────────────
    "taker_buy_ratio_base",
    "delta_taker_pressure",
    "vol_ratio_24",
    "trades_ratio_24",
    "trade_intensity",      # trades/volume — retail vs institutionnel
    # ── Temporel ─────────────────────────────────────────────────────────────
    "hour_sin", "hour_cos",
    "dow_sin", "dow_cos",
]

# ─────────────────────────────────────────────────────────────────────────────
# FEATURES SUPPLÉMENTAIRES LONG — momentum, tendance haussière
# ─────────────────────────────────────────────────────────────────────────────
# Le long exploite les accélérations momentum positives et les tendances établies.
# RSI faible (momentum) et EMAs alignées à la hausse sont des signaux favorables.

FEATURES_LONG_EXTRA: List[str] = [
    # ── Groupe 1 : Price structure — returns & trend ──────────────────────────
    "mom_logret_4",    # 4h momentum — aligné sur l'horizon de prédiction
    "mom_logret_6",
    "mom_logret_12",
    "mom_logret_24",
    "mom_logret_72",
    "dist_ema_20",
    "dist_ema_50",
    "dist_ema_200",
    "ema_spread_20_50",
    "ema_spread_50_200",
    "rsi_14",
    "cci_20",
    # ── Groupe 2 : Price structure — breakout & persistence ───────────────────
    "dist_from_local_low_24",
    "dist_from_local_low_168",
    "breakout_strength_24",
    "trend_persistence_12",
    "ret_pos_autocorr_12",
    "upside_vol_ratio_24",
    "momentum_accel_6",
    "boll_expansion_6",
    # ── Groupe 3 : Flow — accumulation acheteuse ──────────────────────────────
    "taker_buy_cumul_12",
    "buy_vol_ratio_6",
    "vol_imbalance",
    # ── Groupe 4 : Events — liquidations ─────────────────────────────────────
    "liq_short_spike_12",   # short squeezes = carburant pour les longs
    "liq_imbalance",        # négatif = plus de shorts liquidés = bullish
    # ── Groupe 5 : Event-driven — EMA crossover ───────────────────────────────
    "days_since_golden_cross",  # ancienneté du golden cross (frais vs établi)
    "gc_fresh",                 # binary : golden cross < 7j (fort signal)
    "dist_ema200_atr",          # dist EMA200 normée par ATR (adapté au régime vol.)
    # ── Groupe 6 : VWAP — proxy institutionnel intraday ──────────────────────
    "dist_vwap_pct",    # (Close - VWAP) / VWAP — pression acheteuse vs vendeurs
    "above_vwap_4h",    # fraction des 4 dernières barres au-dessus du VWAP
]
# Groupe 7 : Positioning / sentiment (bundle)
FEATURES_LONG_EXTRA = list(dict.fromkeys(FEATURES_LONG_EXTRA + FEATURES_MACRO_LONG))

# ─────────────────────────────────────────────────────────────────────────────
# FEATURES SUPPLÉMENTAIRES SHORT — retournement, surachat, pression vendeuse
# ─────────────────────────────────────────────────────────────────────────────
# Le short exploite les extensions excessives, le surachat et la pression
# vendeuse latente. Les signaux de retournement sont différents des signaux
# de continuation — asymétrie structurelle du marché crypto.

FEATURES_SHORT_EXTRA: List[str] = [
    # ── Groupe 1 : Price structure — surachat & extension ─────────────────────
    "rsi_14",
    "cci_20",
    "boll_pos_20",
    "dist_ema_20",
    "dist_ema_50",
    "mom_logret_24",
    "mom_logret_6",
    # ── Groupe 2 : Price structure — retournement ─────────────────────────────
    "rsi_14_above_70_bars",
    "dist_from_local_high_24",
    "dist_from_local_high_168",
    "ret_neg_autocorr_12",
    "skew_ret_12",
    "skew_ret_24",
    "downside_vol_ratio_24",
    "max_drawdown_12",
    "rv_ratio_24_72",
    "rv_ratio_12_48",
    "zscore_close_24",
    "eff_ratio_12",
    # ── Groupe 3 : Flow — pression vendeuse ──────────────────────────────────
    "delta_taker_cumul_12",
    "sell_vol_ratio_6",
    "sell_vol_ratio_24",
    "price_vol_divergence_12",
    "vol_imbalance",
    # ── Groupe 4 : Events — liquidations ─────────────────────────────────────
    "liq_long_spike_12",    # cascades de liquidation longs = signal short
    "liq_imbalance",        # positif = plus de longs liquidés = bearish
]
# Groupe 5 : Positioning / foule extrême (bundle)
FEATURES_SHORT_EXTRA = list(dict.fromkeys(FEATURES_SHORT_EXTRA + FEATURES_MACRO_SHORT))

# ─────────────────────────────────────────────────────────────────────────────
# SETS COMPLETS PAR BRANCHE
# ─────────────────────────────────────────────────────────────────────────────
# dict.fromkeys préserve l'ordre et déduplique sans trier.

# ─────────────────────────────────────────────────────────────────────────────
# FEATURES RÉGIME BEAR — modèle méta qui gate le short
# ─────────────────────────────────────────────────────────────────────────────
# Uniquement des features macro-structurelles à horizon moyen (24h–168h).
# Pas de features microstructure (orderflow, taker ratio) — trop bruitées
# pour décider si le MARCHÉ est bearish vs bullish.
# Ces features sont toutes disponibles dans FEATURES_LONG (pas de nouvelles colonnes).

FEATURES_REGIME: List[str] = [
    # ── Groupe 1 : Structure EMA — le cœur du label structurel ───────────────
    # Le label = SHORTABLE = (dist_ema_50 < 0) ET (ema_spread < 0) ET (rsi < 48)
    # Le modèle apprend à prédire cette condition avec PLUS de features que la règle.
    "dist_ema_50",          # position vs EMA50 : feature la plus prédictive du label
    "ema_spread_50_200",    # EMA50/EMA200 : death cross / golden cross
    "dist_ema_200",         # position vs EMA200 : régime long terme
    "ema_spread_20_50",     # EMA20/EMA50 : tendance court terme (précède le 50/200)
    # ── Groupe 2 : Momentum multi-horizon ─────────────────────────────────────
    "mom_logret_24",        # trend 24h — capture début de correction
    "mom_logret_72",        # trend 3j — aligné avec le label structurel
    "mom_sharpe_24",        # momentum sharpe-normalisé — direction + intensité
    # ── Groupe 3 : Oscillateurs ───────────────────────────────────────────────
    "rsi_14",               # RSI < 48 = condition structurelle du label
    # ── Groupe 4 : Pression vendeuse (NOUVELLE — non utilisée par la règle) ───
    # Ces features apportent de l'information au-delà de la règle déterministe.
    "delta_taker_cumul_12", # accumulation vendeuse nette : pression bear micro
    "sell_vol_ratio_24",    # ratio volume vendeur 24h : confirmation institutionnelle
    "dist_from_local_high_24",  # recul depuis le sommet récent : structure de retournement
    # ── Groupe 5 : Volatilité ─────────────────────────────────────────────────
    "rv_ratio_24_72",       # spike vol = stress = signal bear potentiel
    # ── Groupe 6 : Macro structurel (bundle) — signal de régime long terme ───
] + FEATURES_MACRO_REGIME


# ─────────────────────────────────────────────────────────────────────────────
# FEATURES MACRO FILTRE — non directionnelles, signalent l'activité crowd
# ─────────────────────────────────────────────────────────────────────────────
# Le filtre Stage 1 décide si une barre EST tradeable (peu importe la direction).
# Les extremes de sentiment, funding et positioning indiquent une activité crowd
# élevée qui précède les mouvements directionnels — information absente de FEATURES_COMMON.
# Ignorées silencieusement si absentes du DataFrame (validate_features filtre).

FEATURES_MACRO_FILTER: List[str] = [
    "funding_rate_z_24",              # funding extrême = foule positionnée, mouvement probable
    "oihist_sumOpenInterest_z_24",    # expansion OI = conviction directionnelle en cours
    "fear_greed_value_z_24",          # extrêmes sentiment = cassures ou retournements imminents
    "global_ls_longShortRatio_z_24",  # positionnement extrême = risque de squeeze
]

FEATURES_FILTER: List[str] = list(dict.fromkeys(FEATURES_COMMON + FEATURES_MACRO_FILTER))

FEATURES_LONG: List[str] = list(dict.fromkeys(
    FEATURES_COMMON + FEATURES_LONG_EXTRA
))

FEATURES_SHORT: List[str] = list(dict.fromkeys(
    FEATURES_COMMON + FEATURES_SHORT_EXTRA
))


# ─────────────────────────────────────────────────────────────────────────────
# VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

def get_feature_overlap() -> dict:
    """
    Retourne l'analyse des chevauchements entre les sets de features.
    Utile pour le diagnostic et les ablations.
    """
    long_only  = set(FEATURES_LONG) - set(FEATURES_SHORT)
    short_only = set(FEATURES_SHORT) - set(FEATURES_LONG)
    both       = set(FEATURES_LONG) & set(FEATURES_SHORT)
    return {
        "common_only":        sorted(set(FEATURES_COMMON)),
        "long_only":          sorted(long_only - set(FEATURES_COMMON)),
        "short_only":         sorted(short_only - set(FEATURES_COMMON)),
        "shared_long_short":  sorted(both),
        "regime_features":    sorted(FEATURES_REGIME),
        "n_filter":           len(FEATURES_FILTER),
        "n_long":             len(FEATURES_LONG),
        "n_short":            len(FEATURES_SHORT),
        "n_regime":           len(FEATURES_REGIME),
    }


# ─────────────────────────────────────────────────────────────────────────────
# FEATURES SHORT GAMECHANGER — calculées par ai/level_0/short_features.py
# ─────────────────────────────────────────────────────────────────────────────
# Ces features sont spécifiques au SHORT stress/breakdown/crowding.
# Ne pas les utiliser pour le LONG — elles mesurent des phénomènes asymétriques.
# Toutes calculables en live sans lookahead.

FEATURES_SHORT_GAMECHANGER: List[str] = [
    # ── Crowding ──────────────────────────────────────────────────────────────
    "funding_extreme_positive",
    "funding_accel_24",
    "funding_accel_72",
    "long_short_extreme",
    "open_interest_expansion",
    "oi_price_divergence",
    "oi_up_price_flat",
    "oi_up_price_down",
    "fear_greed_extreme",
    "long_crowding_score",
    # ── Breakdown ─────────────────────────────────────────────────────────────
    "breakdown_strength_24",
    "breakdown_strength_168",
    "below_vwap_4h",
    "below_vwap_12h",
    "vwap_loss_event",
    "below_ema20",
    "below_ema50",
    "below_ema200",
    "ema_stack_bearish",
    "local_low_break_24",
    "local_low_break_168",
    "downside_vol_ratio_24",
    "rv_downside_24",
    "breakdown_score",
    # ── Failed breakout ───────────────────────────────────────────────────────
    "failed_high_6",
    "failed_high_12",
    "failed_high_24",
    "upper_wick_pct",
    "upper_wick_z_24",
    "close_rejection_from_high",
    "volume_exhaustion_high",
    "taker_buy_exhaustion",
    "bull_trap_score",
    "failed_breakout_score",
    # ── Liquidity stress ──────────────────────────────────────────────────────
    "liq_long_spike_12",
    "liq_long_spike_24",
    "liq_imbalance_short",
    "spread_proxy",
    "range_expansion_6",
    "range_expansion_24",
    "downside_range_expansion",
    "sell_volume_shock",
    "taker_sell_cumul_12",
    "taker_sell_pressure",
    # ── Squeeze risk ──────────────────────────────────────────────────────────
    "squeeze_risk_score",
    "positive_momentum_accel",
    "price_above_vwap",
    "funding_negative_squeeze",
    "taker_buy_pressure",
    "reclaim_vwap_event",
    "rsi_recovery_from_oversold",
    "short_late_entry_risk",
    # ── Scores composites ─────────────────────────────────────────────────────
    "bear_continuation_score",
    "weak_bounce_score",
]


def validate_features(df: "pd.DataFrame", feature_list: List[str],
                      context: str = "") -> None:
    """
    Vérifie que toutes les features requises sont présentes et non-NaN.
    Lève RuntimeError immédiatement — ne laisse pas les NaN se propager.

    Appeler avant tout entraînement ou inférence.
    """
    ctx = f" [{context}]" if context else ""

    missing = [f for f in feature_list if f not in df.columns]
    if missing:
        raise RuntimeError(
            f"Features manquantes{ctx}: {missing}\n"
            f"Vérifier le CSV de features et le feature engineering."
        )

    nan_counts = df[feature_list].isna().sum()
    bad_cols = nan_counts[nan_counts > 0].to_dict()
    if bad_cols:
        raise RuntimeError(
            f"NaN dans les features{ctx}: {bad_cols}\n"
            f"Appliquer dropna ou imputation avant l'entraînement."
        )
