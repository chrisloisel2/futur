"""
level_0/features.py — LISTES DE FEATURES PAR BRANCHE
=====================================================

Trois groupes distincts et explicites :
  FEATURES_COMMON  : features utilisées par le FILTRE, LONG et SHORT
  FEATURES_LONG    : COMMON + features favorables au long (momentum, trend)
  FEATURES_SHORT   : COMMON + features favorables au short (reversal, surachat)

Modules techniques avancés (technical_indicators.py) :
  FEATURES_ICHIMOKU_LONG  : nuage de Ichimoku — contexte haussier
  FEATURES_ICHIMOKU_SHORT : nuage de Ichimoku — contexte baissier
  FEATURES_RSI_LONG       : RSI multi-période + divergences bull
  FEATURES_RSI_SHORT      : RSI multi-période + divergences bear
  FEATURES_VOLUME_LONG    : OBV, CMF, MFI, climax — accumulation
  FEATURES_VOLUME_SHORT   : OBV, CMF, MFI, climax — distribution

Règle : ne jamais supposer que long et short ont les mêmes features importantes.

Pour ajouter une feature :
  1. L'ajouter dans le groupe le plus pertinent
  2. Vérifier qu'elle existe dans le CSV de référence
  3. Relancer les backtests avant/après pour confirmer l'impact
"""
from __future__ import annotations

from typing import List
import pandas as pd

# Import différé pour éviter les imports circulaires — short_features.py
# ne dépend pas de features.py, import direct sûr.
from ai.level_0.short_features import FEATURES_SHORT_GAMECHANGER  # noqa: E402

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
    "global_market_cap_usd_z_72",     # cycle crypto global, indépendant du seul BTC
    "news_count_z_72",                # pression médiatique persistante
    # Cross-macro 72h (hedge_fund bundle) — signal de régime enrichi
    "macro_regime_score",             # score composite [-2,+2] : positif=bull, négatif=bear
    "crowd_leverage_index",           # levier crowd composite : |funding|×|L/S| → stress
]

FEATURES_MACRO_LONG: List[str] = [
    # Signaux bull court terme (24h) — confirment l'élan haussier
    "funding_rate_z_24",           # funding positif = bullish crowd
    "oihist_sumOpenInterest_z_24", # OI monte avec le prix = confirmation
    "fear_greed_value_z_24",       # greed = momentum sentiment
    "taker_ls_imbalance",          # acheteurs agressifs > vendeurs
    "oi_x_fng",                    # OI × F&G : double confirmation sentiment+positions
    "global_market_cap_usd_z_24",  # expansion/contraction du marché crypto global
    "btc_dominance_z_24",          # rotation BTC vs altcoins
    "btc_mempool_fee_fastest_z_24", # congestion BTC comme proxy d'activité on-chain
    "btc_mempool_tx_count_z_24",
    "news_count_z_24",             # intensité médiatique court terme
    # Cross-macro long (hedge_fund bundle) — nouvelles edges
    "oi_acceleration_z",           # OI qui accélère = conviction croissante = bull
    "macro_confluence_long",       # 0-5 signaux bullish alignés : plus = meilleur setup
    "oi_funding_divergence",       # OI monte + crowd short = accumulation smart money
    "macro_regime_score",          # score macro composite : > 0.5 = confirmation long
]

FEATURES_MACRO_SHORT: List[str] = [
    # Signaux de foule extrême et épuisement — contra indicators pour le short
    "funding_rate_z_24",           # funding trop positif = foule longée → fade
    "funding_x_global_ls",         # foule doublement longée (funding + L/S)
    "fear_greed_value_z_24",       # greed extrême = retournement imminent
    "taker_ls_buySellRatio_z_24",  # ratio taker z-scoré (divergence = top)
    "global_ls_longShortRatio_z_24", # L/S élevé = positionnement contrarian
    "news_count_z_24",             # euphorie/panique médiatique
    "btc_mempool_fee_fastest_z_24",
    # Cross-macro short (hedge_fund bundle) — nouvelles edges
    "macro_confluence_short",      # 0-5 signaux bearish alignés : plus = meilleur setup short
    "crowd_leverage_index",        # levier crowd extrême = squeeze risk = opportunité short
    "oi_acceleration_z",           # OI accélère sur fond de funding extrême = distribution imminente
    "macro_regime_score",          # score macro < -0.5 = confirmation short
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
# TRADINGVIEW SCRIPTS — portés depuis Pine Script open-source
# ─────────────────────────────────────────────────────────────────────────────
# 8 indicateurs communautaires. Chaque feature apporte une lecture orthogonale :
#   Squeeze → compression/explosion, Supertrend → direction ATR-adaptive,
#   WaveTrend → oscillateur de retournement, ADX → force de tendance,
#   HMA → tendance sans lag, ZLMACD → momentum réactif,
#   LSMA+R² → qualité du trend linéaire, Chandelier → niveau de sortie ATR

FEATURES_TV_LONG: List[str] = [
    # Squeeze Momentum
    "sqz_in_squeeze",        # 1 = compression en cours (attendre le breakout haussier)
    "sqz_momentum",          # positif = breakout haussier probable
    "sqz_momentum_accel",    # accélération confirme la direction
    "sqz_on_release",        # 1 = vient de sortir de squeeze → momentum entrant
    # Supertrend
    "supertrend_dir",        # +1 = uptrend  confirmé par ATR
    "supertrend_dist",       # > 0 : close au-dessus du support ST (bull zone)
    "supertrend_flip",       # +1 = nouveau signal haussier
    # WaveTrend
    "wt1",                   # bas (<-53) = oversold → rebond potentiel
    "wt_diff",               # croisement wt1/wt2 = signal directionnel
    "wt_oversold",           # 1 = zone de retournement haussier fort
    # ADX
    "adx_14",                # force de la tendance (> 25 = valide)
    "di_diff",               # > 0 = +DI dominant = pression haussière
    "adx_trending",          # 1 = marché directionnel → long valide
    # HMA
    "hma_dist",              # > 0 = close au-dessus du HMA (tendance bull)
    "hma_slope",             # positif = HMA monte = momentum haussier
    # ZLMACD
    "zlmacd_hist",           # > 0 = momentum haussier accéléré
    "zlmacd_slope",          # accélération de l'histogramme
    # LSMA
    "lsma_dist",             # > 0 = close au-dessus du trend linéaire
    "lsma_slope",            # positif = trend linéaire monte
    "lr_r2",                 # > 0.5 = strong trend (signal reliable)
    # Chandelier
    "chandelier_long_dist",  # > 0 = close au-dessus du stop trail → zone bull
]

FEATURES_TV_SHORT: List[str] = [
    # Squeeze Momentum
    "sqz_in_squeeze",        # 1 = compression avant potentiel breakdown
    "sqz_momentum",          # négatif = breakdown baissier probable
    "sqz_momentum_accel",    # accélération baissière
    "sqz_on_release",        # 1 = sortie de squeeze → mouvement entrant
    # Supertrend
    "supertrend_dir",        # -1 = downtrend confirmé
    "supertrend_dist",       # < 0 = close en-dessous de la résistance ST (bear zone)
    "supertrend_flip",       # -1 = nouveau signal baissier
    # WaveTrend
    "wt1",                   # haut (>53) = overbought → retournement potentiel
    "wt_diff",               # croisement baissier wt1/wt2
    "wt_overbought",         # 1 = zone de retournement baissier fort
    # ADX
    "adx_14",                # force de la tendance baissière
    "di_diff",               # < 0 = -DI dominant = pression baissière
    "adx_trending",          # 1 = downtrend directionnel → short valide
    # HMA
    "hma_dist",              # < 0 = close en-dessous du HMA (tendance bear)
    "hma_slope",             # négatif = HMA descend = momentum baissier
    # ZLMACD
    "zlmacd_hist",           # < 0 = momentum baissier accéléré
    "zlmacd_slope",          # accélération baissière
    # LSMA
    "lsma_dist",             # < 0 = close en-dessous du trend linéaire
    "lsma_slope",            # négatif = trend descend
    "lr_r2",                 # > 0.5 = strong downtrend
    # Chandelier
    "chandelier_short_dist", # < 0 = close en-dessous du stop short → zone bear
]


# ─────────────────────────────────────────────────────────────────────────────
# ICHIMOKU — Nuage de Ichimoku (technical_indicators.compute_ichimoku_features)
# ─────────────────────────────────────────────────────────────────────────────
# Le nuage donne un avis structurel sur le marché : tendance, support/résistance,
# momentum Tenkan/Kijun, et confirmation Chikou.
# Asymétrie long/short : above_cloud/tk_bullish pour le long, below_cloud/tk_bearish
# pour le short. Les distances et épaisseur sont partagées (contexte neutre).

FEATURES_ICHIMOKU_LONG: List[str] = [
    "ichi_tenkan_dist",     # distance close/Tenkan : momentum court terme
    "ichi_kijun_dist",      # distance close/Kijun  : ancrage moyen terme
    "ichi_cloud_dist_top",  # au-dessus du nuage = extension haussière
    "ichi_cloud_dist_bot",  # distance au support du nuage
    "ichi_cloud_thickness", # nuage épais = tendance forte
    "ichi_above_cloud",     # signal binaire fort : price > cloud (bull setup)
    "ichi_in_cloud",        # zone d'indécision (filter out weak signals)
    "ichi_cloud_bullish",   # nuage vert = contexte structurellement haussier
    "ichi_tk_bullish",      # Tenkan > Kijun = élan haussier court terme
    "ichi_chikou_dist",     # confirmation Chikou (close vs close[t-26])
]

FEATURES_ICHIMOKU_SHORT: List[str] = [
    "ichi_tenkan_dist",     # distance close/Tenkan (négatif = bearish pressure)
    "ichi_kijun_dist",      # distance close/Kijun  (résistance potentielle)
    "ichi_cloud_dist_top",  # résistance : prix sous le nuage
    "ichi_cloud_dist_bot",  # extension baissière sous le nuage
    "ichi_cloud_thickness", # nuage épais = résistance forte au retour haussier
    "ichi_below_cloud",     # signal binaire fort : price < cloud (bear setup)
    "ichi_in_cloud",        # zone d'indécision (éviter les faux shorts)
    "ichi_cloud_bullish",   # nuage rouge (0) = contexte structurellement baissier
    "ichi_tk_bearish",      # Tenkan < Kijun = élan baissier court terme
    "ichi_chikou_dist",     # confirmation Chikou (négatif = baissier)
]


# ─────────────────────────────────────────────────────────────────────────────
# RSI ÉTENDU — (technical_indicators.compute_rsi_features)
# ─────────────────────────────────────────────────────────────────────────────
# Au-delà du RSI-14 existant : multi-période, divergences et Stochastic RSI.
# Les divergences sont les signaux les plus précieux : elles précèdent les moves.

FEATURES_RSI_LONG: List[str] = [
    "rsi_7",               # RSI rapide : surréactions et rebonds immédiats
    "rsi_21",              # RSI lent   : structure de tendance intermédiaire
    "rsi_slope_6",         # accélération RSI → élan haussier qui s'installe
    "rsi_divergence_bull", # RSI monte plus vite que le prix → force cachée
    "rsi_oversold_bars",   # profondeur de l'oversold → rebond élastique potentiel
    "stoch_rsi_k",         # Stochastic RSI K : zone oversold < 0.2 = rebond
    "stoch_rsi_d",         # signal lissé du Stoch RSI
]

FEATURES_RSI_SHORT: List[str] = [
    "rsi_7",               # RSI rapide : extensions excessives
    "rsi_21",              # RSI lent   : overbought structural
    "rsi_slope_6",         # pente RSI : décélération = épuisement haussier
    "rsi_divergence_bear", # RSI baisse plus vite que le prix → faiblesse cachée
    "stoch_rsi_k",         # zone overbought > 0.8 = retournement potentiel
    "stoch_rsi_d",         # signal lissé du Stoch RSI
]


# ─────────────────────────────────────────────────────────────────────────────
# VOLUMES AVANCÉS — (technical_indicators.compute_volume_features)
# ─────────────────────────────────────────────────────────────────────────────
# L'analyse de volumes apporte l'information la plus orthogonale au price-action.
# OBV et A/D captent les institutionnels. CMF/MFI quantifient la conviction.
# Climax détecte les points d'épuisement. Dry-up signale les consolidations.

FEATURES_VOLUME_LONG: List[str] = [
    "obv_slope_12",    # flux OBV positif = accumulation nette
    "cmf_20",          # CMF > 0 : pression acheteuse (close hauts dans le range)
    "mfi_14",          # MFI bas (<30) = oversold volumétrique → rebond
    "ad_slope_12",     # A/D monte = smart money accumule
    "vol_climax_sell", # capitulation (vol spike + close bas) → retournement long
    "vol_dry_up_12",   # consolidation silencieuse avant breakout haussier
    "vol_delta_accel", # passage de pression vendeuse → acheteuse
]

FEATURES_VOLUME_SHORT: List[str] = [
    "obv_slope_12",    # flux OBV négatif = distribution nette
    "cmf_20",          # CMF < 0 : pression vendeuse (close bas dans le range)
    "mfi_14",          # MFI haut (>70) = overbought volumétrique → retournement
    "ad_slope_12",     # A/D baisse = smart money distribue
    "vol_climax_buy",  # épuisement (vol spike + close haut) → retournement short
    "vol_dry_up_12",   # distribution silencieuse avant breakdown
    "vol_delta_accel", # passage de pression acheteuse → vendeuse (inversé)
]


# ─────────────────────────────────────────────────────────────────────────────
# FEATURES SUPPLÉMENTAIRES LONG — momentum, tendance haussière
# ─────────────────────────────────────────────────────────────────────────────
# Le long exploite les accélérations momentum positives et les tendances établies.
# RSI faible (momentum) et EMAs alignées à la hausse sont des signaux favorables.

FEATURES_LONG_EXTRA: List[str] = [
    # ── Groupe 1 : Price structure — returns & trend ──────────────────────────
    "mom_logret_4",    # 4h momentum — contexte court terme
    "mom_logret_8",    # 8h momentum — aligné sur l'horizon de prédiction
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
# Groupe 8 : Indicateurs techniques avancés (Ichimoku, RSI étendu, Volumes)
FEATURES_LONG_EXTRA = list(dict.fromkeys(
    FEATURES_LONG_EXTRA
    + FEATURES_MACRO_LONG
    + FEATURES_TV_LONG
    + FEATURES_ICHIMOKU_LONG
    + FEATURES_RSI_LONG
    + FEATURES_VOLUME_LONG
))

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
# Groupe 6 : Indicateurs techniques avancés (Ichimoku, RSI étendu, Volumes)
FEATURES_SHORT_EXTRA = list(dict.fromkeys(
    FEATURES_SHORT_EXTRA
    + FEATURES_MACRO_SHORT
    + FEATURES_TV_SHORT
    + FEATURES_ICHIMOKU_SHORT
    + FEATURES_RSI_SHORT
    + FEATURES_VOLUME_SHORT
    # Gamechanger short : 55 features contrariantes de haute valeur
    # (crowding, breakdown, failed_breakout, liquidity_stress, squeeze_risk)
    # Calculées par compute_all_short_features() depuis short_features.py.
    + FEATURES_SHORT_GAMECHANGER
))

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
    "global_market_cap_usd_z_24",     # activité de marché globale
    "btc_dominance_z_24",
    "news_count_z_24",
    # Cross-macro filter (hedge_fund bundle) — les plus prédictifs de la tradeabilité
    "taker_ls_imbalance",             # déséquilibre taker net : marché en train de se décider
    "oi_x_fng",                       # OI × F&G : double conviction = barre très tradeable
    "crowd_leverage_index",           # levier extrême = mouvement explosif probable
    "macro_confluence_long",          # nb signaux bull alignés : au-dessus de 2 = barre active
    "macro_confluence_short",         # nb signaux bear alignés : au-dessus de 2 = barre active
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


def get_available_features(
    df: "pd.DataFrame",
    feature_list: List[str],
    min_fill: float = 0.0,
    context: str = "",
) -> List[str]:
    """
    Filtre feature_list aux seules colonnes présentes dans df avec fill ≥ min_fill.

    Utiliser à l'entraînement pour éviter de passer des features toujours-zero
    au modèle (ex: colonnes macro absentes des CSV d'entraînement).

    Arguments
    ---------
    df           : DataFrame d'entraînement
    feature_list : liste candidate
    min_fill     : fraction minimale de valeurs non-NaN (défaut 0.0 = présent suffit)
    context      : label pour le logging

    Retourne
    --------
    Liste filtrée, dans le même ordre, sans doublons.
    """
    n = len(df)
    available = []
    n_absent = 0
    n_sparse = 0
    for f in feature_list:
        if f not in df.columns:
            n_absent += 1
            continue
        if min_fill > 0.0 and n > 0:
            fill = df[f].notna().sum() / n
            if fill < min_fill:
                n_sparse += 1
                continue
        available.append(f)

    if n_absent > 0 or n_sparse > 0:
        ctx = f" [{context}]" if context else ""
        print(
            f"   get_available_features{ctx}: "
            f"{len(available)}/{len(feature_list)} features utilisées "
            f"({n_absent} absentes, {n_sparse} trop sparse)"
        )
    return available
