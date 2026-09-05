#!/usr/bin/env python3
"""W9 — assemble RESULTS.json a partir de tous les JSON produits dans le scratch."""
import json, os, glob, collections
SC = os.environ.get("W9_OUT", "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad")
HERE = os.path.dirname(os.path.abspath(__file__)); DST = os.path.dirname(HERE)

def load(p, default=None):
    try: return json.load(open(os.path.join(SC, p)))
    except Exception: return default

mech = load("mech_results_v2.json", {})
out = {
 "worker": "W9_ENRICHED_DEEP_MINE",
 "round": "alpha_hunt_2026-09-03_round4",
 "date_report": "2026-09-05",
 "axis": "data/enriched/*_1h_enriched.parquet",
 "headline": ("Le livrable est la table d'audit des 4178 colonnes (evidence/COLUMN_VERDICTS.csv). "
              "Cote alpha : 0 VALIDATED_FOR_FORWARD. Le seul candidat qui passait le gate "
              "preenregistre a ete tue par un test PLACEBO ajoute a la relecture."),
 "phase1_audit": {
   "n_columns_total": 4178, "n_symbols": 50, "n_symbols_column_audited": 7,
   "bars": "1h", "span": "2017-08-17 -> 2026-09-04",
   "verdict_counts": {"USABLE": 2911, "REDUNDANT_ALIAS": 816, "PLACEHOLDER": 264,
                      "NOT_UNIVERSAL": 142, "DEGRADED_PERIOD": 36, "METADATA": 6, "LOOKAHEAD": 3},
   "table": "evidence/COLUMN_VERDICTS.csv",
   "by_family": "evidence/AUDIT_SUMMARY_BY_FAMILY.csv",
   "never_use": "evidence/NEVER_USE.csv",
   "key_findings": [
     "taker_buy_quote_asset_volume / taker_buy_quote / taker_buy_ratio_quote / taker_sell_quote = quote_asset_volume*0.5 EXACTEMENT, 100% des barres, 50/50 symboles, toutes annees.",
     "taker_buy_base_* reel seulement pour ADA, AVAX, BNB, DOGE, LINK, SOL, XRP (+ queue 2026 de BTC, DOT, ETH).",
     "number_of_trades = 0 sauf 9 symboles ; `trades` en est un alias bit-a-bit.",
     "future_ret_8h == log(close[t+8]/close[t]) a 1e-15 pres : LOOKAHEAD, et perime ~27 jours avant la fin de fichier.",
     "exit_pressure_score_* (11 colonnes) identiquement 0 : bug de generateur, enriched_ohlcv_features.py:899 cherche `reversal_score_<n>` au lieu de `reversal_score`.",
     "816 colonnes sont des alias bit-a-bit d'une autre colonne.",
     "140 des 264 placeholders sont les variantes de fenetre _1 ; regle : n'utiliser que les suffixes >= 14.",
     "Aucun lookahead dans les features : generateur relu, toutes les fenetres .rolling() sont trailing ; le bloc _label_features (19 familles en shift(-n)) n'est PAS materialise.",
     "hour_of_day est bien l'heure UTC (100%).",
     "Bascule de source PERP -> SPOT : BTC au 2026-01-01, ADA/AVAX/BNB/ETH/LINK/SOL au 2026-05-20, DOGE/XRP au 2026-05-24, DOT au 2026-06-28. DOGE et XRP sont SPOT sur tout leur historique.",
     "L'univers des 50 fichiers est la liste frozen-50 de 2026 appliquee retroactivement : aucun symbole deliste. Non point-in-time.",
     "Le volume concorde a 100% avec futur-data-v2 partout ou le close concorde (252 couples symbole x annee, 0 divergence)."
   ],
   "cross_reference": {
     "CROSS_SECTIONAL_MOMENTUM_CVD": "INNOCENTE — CVD construit depuis data_v2, placeholder enrichi documente explicitement dans le rapport.",
     "CROSS_SECTIONAL_MOMENTUM_LIVE_V1": "INNOCENTE — freeze_spec declare ne jamais toucher taker_buy_*.",
     "ai/level_0/institutional_features.py": "A VERIFIER — FEATURES_INST_LONG declare taker_buy_ratio_base et taker_buy_ratio_quote ; sur BTCUSDT ces colonnes valent 0.5 constant sur toute la periode d'entrainement."
   }
 },
 "phase2_mechanisms": mech.get("mechanisms", []),
 "phase2_arm_differences": mech.get("arm_differences", []),
 "phase2_hour_interaction": mech.get("hour_interaction", []),
 "phase2_decile_sweep": load("decile_screen_v2.json"),
 "phase2_pit_replication": load("pit_replication.json"),
 "phase2_bounce_test": load("bounce_test.json"),
 "phase2_plausibility": load("plausibility.json"),
 "phase2_placebo_and_decay": load("placebo_and_decay.json"),
 "phase2_artifact_diagnosis": load("artifact_diagnosis.json"),
 "phase2_artifact_isolation": load("artifact_isolation.json"),
 "phase2_control_level_test": load("control_level.json"),
 "phase2_rerun_corrected": load("rerun_corrected.json"),
 "verdicts_delivered": {
   "H1a_compression_breakout_long": "DEAD",
   "H1b_compression_breakdown_short": "DEAD",
   "H2a_upper_wick_exhaustion_short": "DEAD",
   "H2b_lower_wick_exhaustion_long": "DEAD (passait le gate preenregistre ; tue par le placebo, cf. REPORT 5.3)",
   "H3a_momentum8h_trending": "COST_FRAGILE",
   "H3b_momentum8h_choppy": "DEAD",
   "H4a_unconfirmed_range_high_short": "DEAD (artefact)",
   "H4b_confirmed_range_high_long": "COST_FRAGILE",
   "H5_hour_interaction": "DATA_LIMITED",
   "SCREEN_range_position_deciles": "DEAD (artefact d'estimateur a 96%)"
 },
 "n_validated_for_forward": 0,
 "methodological_finding": {
   "title": "Le controle par JOUR CALENDAIRE fabrique jusqu'a +80 bps d'edge fictif sur un panel intraday",
   "evidence": "evidence/placebo_and_decay.py, artifact_diagnosis.py, artifact_isolation.py, control_level_test.py",
   "mechanism": ("Les evenements se concentrent sur certaines heures ; le controle moyenne les 24 heures du jour. "
                 "Le residu de facteur marche passe integralement dans l'edge. Un placebo qui permute le signal "
                 "entre symboles a instant egal (donc conserve le QUAND) reproduit l'edge a l'identique : "
                 "+79.3 bps contre +82.4 pour le vrai signal, t=16.8 contre 16.6."),
   "fix": ("(1) demeaner a la MEME BARRE HORAIRE, pas au jour ; (2) doubler chaque bras d'un placebo par "
           "permutation entre symboles a instant egal et exiger qu'il soit nul. Apres correction, sur 48 bras "
           "placebo : |t| median 0.57, p90 1.31, max 3.09."),
   "impact": ("Sous controle horaire, H2b passe de +42.8 bps nets (t=8.5) a -4.7 bps (t=-0.83) ; le screen "
              "de position dans le range passe de +115 bps (t=17.5) a -13.5 bps ; et le SIGNE de la conclusion "
              "preenregistree de H3 et H4b s'inverse.")
 },
}
json.dump(out, open(DST+"/RESULTS.json","w"), indent=1, ensure_ascii=False, default=str)
print("RESULTS.json ecrit,", os.path.getsize(DST+"/RESULTS.json")//1024, "Ko")
c = collections.Counter(m.get("verdict") for m in out["phase2_mechanisms"])
print("verdicts phase 2 (gate preenregistre, avant override placebo):", dict(c))
