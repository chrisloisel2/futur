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
DEPLOYMENT_STATUS = "NOT_DEPLOYABLE"
DEPLOYMENT_REASON = "long_only_insufficient_sample"

# Résultats connus du dernier backtest (mis à jour manuellement après chaque run)
LONG_KNOWN_METRICS = {
    "n_trades":            26,
    "profit_factor":       5.3153,
    "win_rate":            0.7692,
    "expectancy_per_trade": 5.5009,
    "sharpe_annualized":   66.9785,
    "max_drawdown_pct":    0.12,
    "total_return_pct":    1.43,
    "data_period":         "2024-2026",
    "note":                "Sharpe irréaliste avec n=26 trades. Ne pas utiliser pour décision.",
}

MIN_LONG_TRADES_FOR_PAPER = 50
MIN_LONG_TRADES_FOR_LIVE  = 100
