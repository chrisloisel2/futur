"""
config/strategy_flags.py — FLAGS CENTRAUX DE STRATÉGIE
=======================================================

Source de vérité unique pour activer/désactiver les branches de trading.

Règle : ne jamais contourner ces flags dans le code opérationnel.
Tout endpoint, backtest, signal live et dashboard doit les respecter.
"""

# ── Branches actives ──────────────────────────────────────────────────────────

SHORT_ENABLED    = False   # SHORT rejeté : PF < 1, expectancy négative
COMBINED_ENABLED = False   # COMBINED rejeté : SHORT entraîne le combined
LONG_ONLY_ENABLED = True   # LONG seul — seule branche opérationnelle

# ── Raisons de rejet (affichées par le dashboard et les endpoints) ────────────

SHORT_DISABLED_REASON  = "unstable PF < 1 across tested years, negative expectancy"
COMBINED_DISABLED_REASON = "COMBINED rejected: SHORT component fails validation"

# ── Gates de validation LONG pour déploiement paper trading ──────────────────

MIN_LONG_TRADES_FOR_DEPLOY = 50    # n_trades minimum
MIN_PROFIT_FACTOR          = 1.20  # profit factor minimum
MIN_EXPECTANCY             = 0.0   # expectancy / trade minimum (>0)
MAX_DRAWDOWN_PCT           = 12.0  # max drawdown en % (valeur absolue)
MIN_YEARLY_PROFIT_FACTOR   = 1.0   # PF minimum pour chaque année testée

# ── Kill-switch risque (vérifiés par le RiskController) ──────────────────────

DAILY_LOSS_LIMIT_PCT       = 3.0   # stop journalier si -3% equity
MAX_CONSECUTIVE_LOSSES     = 4     # stop après 4 pertes consécutives
COOLDOWN_BARS_AFTER_LOSS   = 6     # barres de repos après perte
