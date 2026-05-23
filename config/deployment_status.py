"""
config/deployment_status.py — STATUT DE DÉPLOIEMENT GLOBAL
===========================================================

Source de vérité unique sur ce qui peut tourner en production.

Règle : ne jamais mettre LIVE_ENABLED = True sans validation complète.
Modifier ici et ici seulement.
"""

LIVE_ENABLED     = False   # interdit jusqu'à validation stricte
PAPER_ENABLED    = True    # paper trading autorisé si long validé
SHORT_ENABLED    = False   # rejeté définitivement (voir REPAIR_REPORT.md)
COMBINED_ENABLED = False   # rejeté définitivement

# Statut actuel du système
DEPLOYMENT_STATUS = "PAPER_TRADING"
DEPLOYMENT_REASON = "long_deployable_btc_eth_6folds_5ok_0cata"

# Résultats walk-forward v5 (2026-05-23, commit c16b81a)
LONG_KNOWN_METRICS = {
    "n_folds":             6,
    "folds_ok":            5,
    "folds_catastrophic":  0,
    "pf_median":           145325884.48,   # médiane dominée par folds à très peu de trades
    "pf_2024":             228.0,
    "pf_2025":             74.0,
    "win_rate_2024":       0.98,
    "win_rate_2025":       0.96,
    "n_trades_2024":       86,
    "n_trades_2025":       80,
    "max_drawdown_pct":    0.0,
    "data_period":         "2020-2025",
    "training_config":     "BTC primary + ETH extra (--max-assets 1)",
    "note":                "PF médian non représentatif (folds 2021-2023 : 1-7 trades 100%WR). "
                           "Utiliser PF 2024-2025 pour évaluation réaliste.",
}

MIN_LONG_TRADES_FOR_PAPER = 50
MIN_LONG_TRADES_FOR_LIVE  = 100
